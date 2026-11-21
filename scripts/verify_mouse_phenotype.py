from pathlib import Path
import csv
import math

root = Path("uploads/u1/ds_80")

fam_path = root / "genotype.fam"
pheno_path = root / "phenotype.txt"

assert fam_path.exists(), "genotype.fam tidak ditemukan"
assert pheno_path.exists(), "phenotype.txt tidak ditemukan"

fam_rows = [
    line.split()
    for line in fam_path.read_text().splitlines()
    if line.strip()
]

pheno_rows = [
    line.split()
    for line in pheno_path.read_text().splitlines()
    if line.strip()
]

print("Jumlah sampel FAM:", len(fam_rows))
print("Jumlah baris phenotype:", len(pheno_rows))

if len(fam_rows) != 1940 or len(pheno_rows) != 1940:
    raise SystemExit("[FAIL] Jumlah baris tidak sesuai.")

if any(len(row) < 11 for row in fam_rows):
    raise SystemExit("[FAIL] Struktur FAM tidak sesuai.")

if any(len(row) != 6 for row in pheno_rows):
    raise SystemExit("[FAIL] Jumlah kolom phenotype tidak konsisten.")

def same_value(a, b):
    if a.upper() == "NA" and b.upper() == "NA":
        return True
    try:
        return math.isclose(
            float(a), float(b),
            rel_tol=1e-12,
            abs_tol=1e-12
        )
    except ValueError:
        return False

matched = 0
mismatches = []

for i, (fam, pheno) in enumerate(zip(fam_rows, pheno_rows), 1):
    fam_phenotypes = fam[5:11]

    if all(
        same_value(a, b)
        for a, b in zip(fam_phenotypes, pheno)
    ):
        matched += 1
    else:
        mismatches.append(i)

print("Baris dengan phenotype cocok:", matched)
print("Baris tidak cocok:", len(mismatches))

if mismatches:
    print("Contoh baris bermasalah:", mismatches[:10])
    raise SystemExit("[FAIL] Urutan atau nilai belum cocok.")

print("\n[OK] Seluruh 1940 phenotype cocok dengan FAM.")
print("[OK] Urutan sampel dapat diverifikasi.")

# Membuat file tabular dengan ID sampel.
output = root / "phenotype_with_ids.tsv"

with output.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, delimiter="\t")

    writer.writerow([
        "FID", "IID",
        "Trait_1", "Trait_2", "Trait_3",
        "Trait_4", "Trait_5", "Trait_6"
    ])

    for fam, pheno in zip(fam_rows, pheno_rows):
        writer.writerow([fam[0], fam[1], *pheno])

print("\n[OK] File baru dibuat:")
print(output.resolve())
print("[INFO] File asli tidak diubah.")
