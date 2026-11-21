# 03. Panduan menghubungkan frontend genosphere-sharing ke backend

Frontend saat ini 100% statis (tidak ada panggilan API). Panduan ini menunjukkan **apa yang dipanggil di tiap halaman**. Berkas pendukung ada di `../../frontend-integration/` (sudah lolos type-check dengan `strict: true` dan Next 16 / React 19 milik proyek).

## 1. Persiapan (sekali)

```bash
# --- Backend (WSL) ---
cd backend && source .venv/bin/activate
pip install -r requirements.txt                       # menambah openpyxl
psql -U postgres -h localhost -d gwas -f sql/004_user_profile.sql   # aman diulang (kolom institution, bio)
uvicorn app.main:app --reload --port 8000             # cek: http://localhost:8000/health -> "schema": "ok"

# --- Frontend ---
cp frontend-integration/lib/api.ts            genosphere-sharing/lib/api.ts
cp frontend-integration/.env.local.example    genosphere-sharing/.env.local
cp -r frontend-integration/app/reset-password genosphere-sharing/app/reset-password
npm run dev
```

Jika `/health` menampilkan `"schema": "needs_migration"`, jalankan migrasi 004 dulu. Jika Next.js jalan di port selain 3000, CORS tetap diizinkan (regex localhost).

## 2. Alur 10 langkah ↔ endpoint

| Halaman frontend | Panggilan (`lib/api.ts`) | Disimpan di store |
|---|---|---|
| `register` | `auth.register({fullName,email,password,confirmPassword})` lalu arahkan ke `/login` | (tidak ada) |
| `login` | `auth.login(identifier, password)` (JSON `/api/login/json`) → simpan `user` lewat `toUserProfile` | `userProfile` |
| `forgot-password` | `auth.forgotPassword(email)` (backend selalu 202) | |
| `reset-password` (baru) | `auth.resetPassword(token, pw, confirm)` | |
| `profile` | `auth.me()` → `toUserProfile`; edit: `auth.updateMe(...)` | `userProfile` |
| **Step 1** | `gwas.uploadGenotype(file, {name, sourceDomain, onProgress})` satu kali per berkas (BED/BIM/FAM diunggah berurutan, `datasetId` respons pertama dipakai untuk berikutnya) → `gwas.uploadPhenotype(file, {datasetId})`; tombol ✕: `gwas.deleteFile(datasetId, slot)`; aktifkan "Lanjut" bila `ready_for_validation` | `datasetId`, daftar `files` |
| **Step 2** | `gwas.validate(datasetId)`; tampilkan `summary_message`, `matching.*`, `errors[]` | hasil validasi |
| **Step 3-4** | `gwas.options()` untuk mengisi dropdown (opsional, atau tetap tulis keras); `gwas.checkConfig({...})` (bisa tiap perubahan dropdown) → `estimate.label` untuk "Estimasi Waktu"; tombol **Jalankan GWAS**: `gwas.createAnalysis({..., execution_mode:'staged'})` | `analysisId` |
| **Step 5** | `pollStatus(id)` atau `openAnalysisStream(id)` sampai `awaiting`; lalu `gwas.qcSummary(id)` → "SNP/Sampel Lolos QC" | |
| **Step 6** | `gwas.stagePca(id,{pca_components:'5 PC',covariates:'Usia, Jenis Kelamin'})` (`use_pca:false` bila "Tanpa PCA"); lalu `gwas.pca(id)` untuk scatter (`points[].pcs[0..1]`, warna dari `trait`) | |
| **Step 7** | `gwas.stageRun(id,{model:'Logistic Regression'})` + `openAnalysisStream(id, onMessage)` (log + persen); selesai saat `state==='done'` | |
| **Step 8** | `gwas.postprocess(id, '0.05 (FDR)')` → `n_significant` | |
| **Step 9** | `gwas.manhattanRows(id)` → `items` (`x`, `neg_log10_p`, `is_significant`), `thresholds`, `chromosome_ticks`; `gwas.top(id,10)`; `gwas.qq(id)` | `gwasResults` (baris sudah bernama `chr,pos,snp,pvalue`) |
| **Step 10** | `gwas.download(id,'Excel (.xlsx)')`; "Simpan Konfigurasi": `gwas.downloadConfig(id)`; riwayat: `gwas.listAnalyses()` | |

Handler 401 (token habis): `setUnauthorizedHandler(() => { auth.logout(); router.push('/login'); })` sekali di layout.

## 3. Contoh kode

**Login** (`login/page.tsx`):
```tsx
const { user } = await auth.login(identifier, password);     // memakai POST /api/login/json {identifier, password}
usePipelineStore.getState().setUserProfile(toUserProfile(user));
router.push('/pipeline/step-1');
```

**Step 1** (`step-1/page.tsx`): ubah input genotipe menjadi `multiple` dan ambil seluruh berkas:
```tsx
<input type="file" multiple accept=".vcf,.vcf.gz,.gz,.bed,.bim,.fam" onChange={e => setGeno(Array.from(e.target.files ?? []))} />
...
const g = await gwas.uploadGenotype(geno, { name: projectName, onProgress: setPct });
const p = await gwas.uploadPhenotype(pheno, { datasetId: g.dataset_id, onProgress: setPct2 });
setReady(p.ready_for_validation);
```

**Step 3-4** (label dropdown dikirim apa adanya):
```tsx
const chk = await gwas.checkConfig({
  dataset_id, trait_name: 'Disease',
  trait_type: tipeTrait,                 // 'Biner (Case / Control)'
  model: modelGwas,                      // 'Logistic Regression'
  parameter_qc: parameterQC,             // 'MAF >= 0.05, Call Rate >= 0.95'
  analysis_goal: tujuanAnalisis,
});
setEstimasi(chk.estimate?.label);        // '~3 menit'
if (!chk.valid) showErrors(chk.errors);  // pesan Indonesia + hint
const run = await gwas.createAnalysis({ ...sameFields, execution_mode: 'staged' });
```

**Step 7** (log real-time):
```tsx
await gwas.stageRun(analysisId, { model: selectedModel });
const ws = openAnalysisStream(analysisId, (m) => {
  if (m.type === 'log') setLogs((l) => [...l, ...m.lines]);
  else { setPct(m.percent); if (m.state === 'failed') setError(m.error?.message); }
}, () => router.push('/pipeline/step-8'));
```

## 4. Bentuk data utama (ringkas)

* **Error**: `{"detail":{"code","message","hint?","errors?":[{field|code,message}]}}` → `ApiError` di `api.ts`.
* **Status**: `{status: queued|running|awaiting|done|failed, stage, percent, message, next_step: 'pca'|'gwas'|null, can_continue, error}`.
* **Baris hasil**: `{chr,pos,snp,pvalue, chromosome,position,rsid,p_value,p_bonferroni,effect_size,odds_ratio,is_significant}`.
* **Manhattan (rows)**: `{items:[{chr,pos,snp,pvalue,neg_log10_p,x,is_significant}], chromosome_ticks:{labels,positions}, thresholds:{genome_wide,suggestive,bonferroni}}`.

## 5. Catatan penting

1. **Mode `staged` vs `auto`.** `auto` (default API) menjalankan semuanya sekali jalan; frontend 10-langkah paling cocok dengan `staged`. Keduanya memberi hasil yang sama.
2. **`dataset_id` / `analysis_id`** perlu disimpan di store (sekarang belum ada) agar bertahan antar halaman; untuk bertahan saat refresh gunakan `gwas.listDatasets()` / `gwas.listAnalyses()`.
3. **Label ambang Step 8**: opsi `5e-8 (Bonferroni)` = ambang genome-wide; untuk Bonferroni sesungguhnya (0,05 / jumlah varian) kirim `'bonferroni'`.
4. **Guided Mode** memakai preset per domain. Frontend belum punya pilihan domain; backend memakai `source_domain` dataset (kirim `sourceDomain` saat upload) atau `domain` eksplisit.
5. **Akurasi estimasi waktu** hanya perkiraan kasar (sampel × varian), bukan jaminan.
6. **Produksi**: ganti `CORS_ORIGINS`, kosongkan `CORS_ORIGIN_REGEX`, pasang HTTPS (token dan WebSocket `ws://` → `wss://`).


### Catatan upload yang harus diikuti

Router backend menerima satu field multipart bernama `file` untuk setiap request upload, bukan field multi-file `files`. Pada frontend, pengguna tetap boleh memilih tiga file PLINK sekaligus melalui file picker, tetapi kirim tiap `File` dengan request terpisah secara berurutan. Request pertama tidak mengirim `dataset_id`; responsnya memberikan `dataset_id`. Dua request berikutnya mengirim ID yang sama. Tunggu respons setiap unggahan agar batas kuota dataset dan validasi slot diterapkan oleh backend.

### PCA dan endpoint ringkasan terbaru

Pilih hanya 5, 10, atau 20 PC. Jangan mengasumsikan `actual_components` selalu sama dengan pilihan pengguna. Tampilkan `status` (`complete`, `partial`, `unavailable`) dan `warnings` dari respons PCA. Endpoint tambahan data usage, ekspor sumber Manhattan/QQ/PCA, ekspor QC, dan interpretasi AI didokumentasikan di `03_KONTRAK_API_UNTUK_FRONTEND.md`.


Unduh hasil interpretasi AI yang telah selesai melalui `GET /api/gwas/analyses/{id}/interpretation/export?format=json`. Endpoint mengirim attachment JSON dan tetap memeriksa pemilik analisis.
