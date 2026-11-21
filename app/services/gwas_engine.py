"""Mesin analisis GWAS (Modul 2 "Workflow Engine" pada LKP 4; UC-03; Step 5 sampai 7 di antarmuka)."""
import json
import hashlib
import math
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GwasResult, Snp
from app.services import validation as V
from app.services.config_rules import MODEL_LABEL
from app.services.tools import ToolError, resolve_binary, run_tool, tool_version
from app.services.resource_policy import check_pca_fallback_memory

CHUNK = 5000                   # jumlah varian per transaksi saat menyimpan hasil
P_FLOOR = sys.float_info.min   # p-value 0 (underflow) diganti nilai terkecil agar -log10(p) tetap berhingga


class PipelineError(Exception):
    """Kesalahan yang sudah diperkirakan (bukan bug). Pesan dan petunjuknya ditampilkan ke pengguna."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


@dataclass
class RunContext:
    """Bekal satu eksekusi: folder dataset, folder keluaran, berkas log, dan fungsi untuk melaporkan progres."""

    ddir: Path
    rdir: Path
    log_path: Path
    progress: Callable[[str, int, str], None]

    def log(self, stage: str, message: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{stage.upper()}] {message}\n")


def _count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for line in f if line.strip())


def _prepare_tables(cfg: dict, ctx: RunContext, input_prefix: Path, with_covariates: bool = True) -> dict:
    """Buat pheno.txt/keep.txt/covar dari target PHENO_ dan kovariat COV_ terpilih.

    FID dan IID dicocokkan sebagai pasangan. IID tetap diwajibkan unik secara global
    karena struktur dictionary PCA/kovariat pipeline saat ini menggunakan IID sebagai key.
    """
    fam: dict[str, str] = {}
    with open(f"{input_prefix}.fam", encoding="utf-8") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) >= 6:
                iid, fid = fields[1], fields[0]
                if iid in fam and fam[iid] != fid:
                    raise PipelineError("DUPLICATE_IID_UNSUPPORTED",
                        f"IID '{iid}' muncul pada lebih dari satu FID di berkas genotipe.",
                        "Gunakan IID yang unik secara global; pipeline saat ini menggunakan IID sebagai kunci internal.")
                fam[iid] = fid

    path = V.find_phenotype_file(ctx.ddir)
    header, rows = V.read_phenotype_table(path)
    fid_idx, iid_idx = V.phenotype_id_columns(header)
    if iid_idx is None:
        raise PipelineError("PHENOTYPE_HEADER_INVALID",
            "Kolom ID individu tidak ditemukan pada berkas fenotipe.",
            "Gunakan IID, INDV, SAMPLE_ID, atau pasangan FID dan IID.")

    header_lookup = {name.casefold(): i for i, name in enumerate(header)}
    requested_trait = str(cfg.get("trait_name") or "").strip()
    t_idx = header_lookup.get(requested_trait.casefold())
    if t_idx is None or t_idx in {fid_idx, iid_idx}:
        raise PipelineError("TRAIT_NOT_FOUND",
            f"Target '{requested_trait or '(kosong)'}' tidak ditemukan sebagai kolom trait.",
            "Pilih nama trait yang tersedia pada endpoint dataset traits.")
    trait_name = header[t_idx]

    def num(value: str) -> float | None:
        if value is None or value.strip().lower() in V.MISSING_TOKENS:
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    cov_list = cfg.get("covariates", []) if with_covariates else []
    specs: list[tuple[int, list[str] | None]] = []
    cov_names: list[str] = []

    def safe_covar_token(value: str) -> str:
        """Membuat token kovariat internal yang konsisten."""
        token = re.sub(r"\W+", "_", str(value).strip()).strip("_").upper()
        return token or "VALUE"
    for requested_name in cov_list:
        requested_name = str(requested_name).strip()
        index = header_lookup.get(requested_name.casefold())
        if index is None:
            raise PipelineError("COVARIATE_NOT_FOUND",
                f"Kovariat '{requested_name}' tidak ditemukan pada berkas fenotipe.",
                "Validasi ulang dataset dan pilih kovariat yang tersedia.")
        name = header[index]
        present = [row[index] for row in rows if row[index].strip().lower() not in V.MISSING_TOKENS]
        if not present:
            raise PipelineError("COVARIATE_NO_VALID_VALUES", f"Kovariat '{name}' seluruh nilainya missing.",
                                "Pilih kovariat lain atau lengkapi nilai pada kolom tersebut.")
        if all(num(value) is not None for value in present):
            specs.append((index, None))
            base_name = name[4:] if name.upper().startswith("COV_") else name
            cov_names.append(f"COV_{safe_covar_token(base_name)}")
        else:
            levels = sorted(set(present))
            if not (2 <= len(levels) <= V.MAX_CATEGORY_LEVELS):
                raise PipelineError("COVARIATE_NOT_SUPPORTED",
                    f"Kovariat kategorikal '{name}' memiliki {len(levels)} kategori; batas yang didukung 2-{V.MAX_CATEGORY_LEVELS}.",
                    "Perbaiki kategori atau jangan gunakan kolom ini sebagai kovariat.")
            specs.append((index, levels))
            base_name = name[4:] if name.upper().startswith("COV_") else name
            safe_base = safe_covar_token(base_name)
            cov_names += [
                f"COV_{safe_base}_{safe_covar_token(level)}"
                for level in levels[1:]
            ]

    def cov_values(row: list[str]) -> list[str] | None:
        output: list[str] = []
        for index, levels in specs:
            value = row[index]
            if value.strip().lower() in V.MISSING_TOKENS:
                return None
            if levels is None:
                number = num(value)
                if number is None:
                    return None
                output.append(repr(number))
            else:
                output += ["1" if value == level else "0" for level in levels[1:]]
        return output

    kept: list[tuple[str, str, float, list[str]]] = []
    accounting = {
        "phenotype_rows": len(rows), "genotype_samples": len(fam),
        "unmatched_no_genotype": 0, "missing_trait": 0, "missing_covariates": 0,
        "retained": 0, "case_count": None, "control_count": None,
    }
    for row in rows:
        required_index = max(iid_idx, fid_idx if fid_idx is not None else iid_idx)
        if len(row) <= required_index:
            accounting["unmatched_no_genotype"] += 1
            continue

        iid = row[iid_idx]
        if not iid or iid not in fam:
            accounting["unmatched_no_genotype"] += 1
            continue

        if fid_idx is None:
            fid = fam[iid]
        else:
            fid = row[fid_idx]
            if not fid or fam[iid] != fid:
                accounting["unmatched_no_genotype"] += 1
                continue
        trait_value = num(row[t_idx])
        if trait_value is None:
            accounting["missing_trait"] += 1
            continue
        cov = cov_values(row)
        if cov is None:
            accounting["missing_covariates"] += 1
            continue
        kept.append((iid, fid, trait_value, cov))

    accounting["retained"] = len(kept)
    if len(kept) < V.MIN_SAMPLES:
        raise PipelineError("TOO_FEW_SAMPLES",
            f"Hanya {len(kept)} sampel dengan pasangan FID/IID, target, dan kovariat lengkap; minimal {V.MIN_SAMPLES}.",
            "Periksa pencocokan FID/IID, missing target, dan missing kovariat yang dipilih.")

    if cfg.get("trait_type") == "binary":
        unique_values = sorted({item[2] for item in kept})
        if unique_values not in ([0.0, 1.0], [1.0, 2.0]):
            raise PipelineError("BINARY_TRAIT_INVALID_CODING",
                f"Target biner '{trait_name}' harus memiliki tepat dua kode: 0/1 atau 1/2; ditemukan {unique_values[:8]}.",
                "Periksa nilai target dan missing sentinel, lalu kodekan kelas sesuai standar PLINK 2.")
        lo, hi = unique_values
        accounting["control_count"] = sum(1 for item in kept if item[2] == lo)
        accounting["case_count"] = sum(1 for item in kept if item[2] == hi)
        from app.services.config_rules import MIN_PER_CLASS, WARN_PER_CLASS
        smallest = min(accounting["case_count"], accounting["control_count"])
        if smallest < MIN_PER_CLASS:
            raise PipelineError("BINARY_CLASS_TOO_SMALL_AFTER_FILTER",
                f"Setelah preprocessing, jumlah kasus={accounting['case_count']} dan kontrol={accounting['control_count']}; masing-masing minimal {MIN_PER_CLASS}.",
                "Periksa missing target/kovariat atau gunakan target dengan jumlah sampel memadai.")
        if smallest < WARN_PER_CLASS:
            ctx.log("preprocessing", f"Peringatan: setelah filtering jumlah kasus={accounting['case_count']} dan kontrol={accounting['control_count']}; hasil dapat kurang stabil.")
        coded = lambda value: 2 if value == hi else 1  # noqa: E731
    else:
        coded = lambda value: value  # noqa: E731

    with open(ctx.rdir / "pheno.txt", "w", encoding="utf-8") as handle:
        handle.write("FID\tIID\tTRAIT\n")
        for iid, fid, trait_value, _ in kept:
            handle.write(f"{fid}\t{iid}\t{coded(trait_value)}\n")
    with open(ctx.rdir / "keep.txt", "w", encoding="utf-8") as handle:
        for iid, fid, _, _ in kept:
            handle.write(f"{fid}\t{iid}\n")

    return {"n_samples": len(kept), "user_cov_names": cov_names,
            "user_cov": {iid: cov for iid, _, _, cov in kept},
            "fid": {iid: fid for iid, fid, _, _ in kept}, "sample_accounting": accounting}



def _write_covar(ctx: RunContext, prep: dict, pcs: dict | None, pc_names: list[str]) -> Path | None:
    names = prep["user_cov_names"] + pc_names
    if not names:
        return None
    path = ctx.rdir / "covar.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("FID\tIID\t" + "\t".join(names) + "\n")
        for iid, fid in prep["fid"].items():
            vals = prep["user_cov"][iid] + (pcs[iid] if pcs else [])
            f.write(f"{fid}\t{iid}\t" + "\t".join(vals) + "\n")
    return path


STAGES = ("qc", "pca", "gwas")


def _setup(ctx: RunContext) -> tuple[str, dict]:
    plink2 = resolve_binary(get_settings().plink2_bin)
    status = V.dataset_status(ctx.ddir)["status"]
    if status == "stale":
        raise PipelineError("DATASET_CHANGED", "Berkas dataset berubah setelah validasi terakhir.", "Jalankan validasi data lagi, lalu ulangi analisis.")
    if status != "valid":
        raise PipelineError("DATASET_NOT_VALIDATED", "Dataset belum lengkap atau belum tervalidasi.", "Lengkapi berkas dan jalankan validasi data terlebih dahulu.")
    return plink2, {"plink2": tool_version(plink2, ["--version"]), "python": sys.version.split()[0]}


def _input_prefix(ctx: RunContext) -> Path:
    files = V.find_genotype_files(ctx.ddir)
    return ctx.rdir / "input" if (files.get(".vcf.gz") or files.get(".vcf")) else ctx.ddir / "genotype"


def _require_qc(ctx: RunContext) -> None:
    if not (ctx.rdir / "qc.bed").exists() or not (ctx.rdir / "qc_summary.json").exists():
        raise PipelineError("STAGE_ORDER", "Tahap QC belum dijalankan untuk analisis ini.", "Jalankan tahap QC terlebih dahulu.")


def _pedigree_summary(fam_path: Path, keep_path: Path | None = None) -> dict:
    """Summarize founder/family counts for the samples active in a run."""
    kept = None
    if keep_path is not None and keep_path.exists():
        kept = set()
        with keep_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) >= 2:
                    kept.add((fields[0], fields[1]))

    samples = []
    with fam_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 6:
                continue
            fid, iid, father, mother = fields[:4]
            if kept is not None and (fid, iid) not in kept:
                continue
            samples.append((fid, iid, father, mother))

    founders = sum(father == "0" and mother == "0" for _, _, father, mother in samples)
    return {
        "samples": len(samples),
        "founders": founders,
        "nonfounders": len(samples) - founders,
        "families": len({fid for fid, _, _, _ in samples}),
    }


def _write_family_reference_keep(
    fam_path: Path,
    keep_path: Path,
    output_path: Path,
    minimum_families: int = 50,
) -> int:
    """Write one deterministic representative per FID for LD/frequency reference only."""
    active = set()
    with keep_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) >= 2:
                active.add((fields[0], fields[1]))

    families: dict[str, list[str]] = {}
    with fam_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 2:
                continue
            fid, iid = fields[:2]
            if (fid, iid) in active:
                families.setdefault(fid, []).append(iid)

    if len(families) < minimum_families:
        return 0

    selected: list[tuple[str, str]] = []
    for fid in sorted(families):
        unique_iids = sorted(set(families[fid]))
        if not unique_iids:
            continue
        # Deterministic pseudo-random representative; same dataset gives same selection.
        iid = min(
            unique_iids,
            key=lambda value: hashlib.sha256(f"{fid}\0{value}".encode("utf-8")).digest(),
        )
        selected.append((fid, iid))

    if len(selected) < minimum_families:
        return 0

    with output_path.open("w", encoding="utf-8") as handle:
        for fid, iid in selected:
            handle.write(f"{fid}\t{iid}\n")
    return len(selected)


def _count_negative_bp_variants(bim_path: Path) -> int:
    """Count variants with negative BP coordinates in a PLINK .bim file."""
    count = 0
    if not bim_path.exists():
        return 0
    with bim_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 4:
                continue
            try:
                count += int(float(fields[3]) < 0)
            except ValueError:
                continue
    return count


def _parse_variant_loader_summary(log_path: Path) -> dict:
    """Parse PLINK's 'excluded ... remaining' summary from a run log."""
    if not log_path.exists():
        return {"available": False}
    content = log_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"(\d+)\s+variants in [^\n]+?;\s*(\d+)\s+excluded by .*?,\s*(\d+)\s+remaining",
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(content))
    if not matches:
        return {"available": False}
    match = matches[-1]
    return {
        "available": True,
        "variants_in_file": int(match.group(1)),
        "excluded_at_load": int(match.group(2)),
        "variants_remaining": int(match.group(3)),
    }










def _summarize_plink_metrics(
    prefix: Path,
    call_rate: float,
    hwe_threshold: float | None = None,
    *,
    maf_path: Path | None = None,
    hwe_path: Path | None = None,
    hwe_unavailable_reason: str | None = None,
    maf_threshold: float | None = None,
) -> dict:
    """Summarize missingness, all-sample MAF, and founder-only HWE reports."""
    def aggregate(path: Path, candidate_columns: tuple[str, ...], threshold: float | None = None):
        if not path.exists():
            return {"available": False, "n_records": 0, "reason": "report_not_found"}
        n = 0
        total = 0.0
        minimum = maximum = None
        above = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            header = handle.readline().lstrip("#").strip().split()
            index = next((header.index(name) for name in candidate_columns if name in header), None)
            if index is None:
                return {"available": False, "n_records": 0, "reason": "metric_column_not_found"}
            for line in handle:
                cols = line.split()
                if len(cols) <= index:
                    continue
                try:
                    value = float(cols[index])
                except (ValueError, OverflowError):
                    continue
                if not math.isfinite(value):
                    continue
                n += 1
                total += value
                minimum = value if minimum is None else min(minimum, value)
                maximum = value if maximum is None else max(maximum, value)
                if threshold is not None and value > threshold:
                    above += 1
        return {
            "available": n > 0,
            "n_records": n,
            "mean": total / n if n else None,
            "min": minimum,
            "max": maximum,
            **({"above_threshold": above, "threshold": threshold} if threshold is not None else {}),
        }

    variant_missing = aggregate(Path(f"{prefix}.vmiss"), ("F_MISS", "MISS_RATE"), 1.0 - call_rate)
    sample_missing = aggregate(Path(f"{prefix}.smiss"), ("F_MISS", "MISS_RATE"), None)

    # --freq is run separately with --nonfounders, so this file reflects all kept samples.
    maf_source = maf_path if maf_path is not None else Path(f"{prefix}.afreq")
    maf = {"available": False, "n_records": 0, "reason": "report_not_found"}
    if maf_source.exists():
        n = 0
        total = 0.0
        minimum = maximum = None
        zero_observation = 0
        invalid = 0
        below = 0
        with maf_source.open("r", encoding="utf-8", errors="replace") as handle:
            header = handle.readline().lstrip("#").strip().split()
            idx = {name: i for i, name in enumerate(header)}
            freq_column = "ALT_FREQS" if "ALT_FREQS" in idx else ("ALT_FREQ" if "ALT_FREQ" in idx else None)
            if freq_column is None:
                maf = {"available": False, "n_records": 0, "reason": "frequency_column_not_found"}
            else:
                for line in handle:
                    cols = line.split()
                    if len(cols) <= idx[freq_column]:
                        invalid += 1
                        continue
                    if "OBS_CT" in idx:
                        try:
                            obs = float(cols[idx["OBS_CT"]])
                        except (ValueError, IndexError):
                            invalid += 1
                            continue
                        if not math.isfinite(obs) or obs <= 0:
                            zero_observation += 1
                            continue
                    try:
                        frequencies = [float(value) for value in cols[idx[freq_column]].split(",")]
                    except (ValueError, IndexError):
                        invalid += 1
                        continue
                    if not frequencies or not all(math.isfinite(value) and 0 <= value <= 1 for value in frequencies):
                        invalid += 1
                        continue
                    alt_total = sum(frequencies)
                    if alt_total > 1.0 + 1e-8:
                        invalid += 1
                        continue
                    ref_frequency = max(0.0, 1.0 - alt_total)
                    value = min([ref_frequency, *frequencies])
                    n += 1
                    total += value
                    minimum = value if minimum is None else min(minimum, value)
                    maximum = value if maximum is None else max(maximum, value)
                    if maf_threshold is not None and value < maf_threshold:
                        below += 1
                maf = {
                    "available": n > 0,
                    "n_records": n,
                    "mean": total / n if n else None,
                    "min": minimum,
                    "max": maximum,
                    "zero_observation_records": zero_observation,
                    "invalid_records": invalid,
                    **({"below_filter_threshold": below, "filter_threshold": maf_threshold}
                       if maf_threshold is not None else {}),
                }
                if n == 0:
                    maf["reason"] = "no_valid_frequency_observations"

    def hwe_report(path: Path) -> dict:
        if hwe_unavailable_reason:
            return {"available": False, "n_records": 0, "reason": hwe_unavailable_reason}
        if not path.exists():
            return {"available": False, "n_records": 0, "reason": "report_not_found"}
        n = 0
        total = 0.0
        minimum = maximum = None
        below = 0
        zero_observation = 0
        invalid = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            header = handle.readline().lstrip("#").strip().split()
            idx = {name: i for i, name in enumerate(header)}
            p_column = next((name for name in ("P", "MIDP", "NEG_LOG10_P", "NEG_LOG10_MIDP") if name in idx), None)
            if p_column is None:
                return {"available": False, "n_records": 0, "reason": "hwe_p_column_not_found"}
            count_columns = ("HOM_A1_CT", "HET_A1_CT", "TWO_AX_CT")
            counts_available = all(name in idx for name in count_columns)
            for line in handle:
                fields = line.split()
                if len(fields) <= idx[p_column]:
                    invalid += 1
                    continue
                if counts_available:
                    try:
                        genotype_count = sum(int(fields[idx[name]]) for name in count_columns)
                    except (ValueError, IndexError):
                        invalid += 1
                        continue
                    if genotype_count <= 0:
                        zero_observation += 1
                        continue
                try:
                    value = float(fields[idx[p_column]])
                    if p_column.startswith("NEG_LOG10_"):
                        value = 10.0 ** (-value) if value < 324 else 0.0
                except (ValueError, OverflowError, IndexError):
                    invalid += 1
                    continue
                if not math.isfinite(value) or value < 0 or value > 1:
                    invalid += 1
                    continue
                n += 1
                total += value
                minimum = value if minimum is None else min(minimum, value)
                maximum = value if maximum is None else max(maximum, value)
                if hwe_threshold is not None and value < hwe_threshold:
                    below += 1
        result = {
            "available": n > 0,
            "n_records": n,
            "mean_p_value": total / n if n else None,
            "min_p_value": minimum,
            "max_p_value": maximum,
            "zero_observation_records": zero_observation,
            "invalid_records": invalid,
            "observation_counts_checked": counts_available,
            **({"below_filter_threshold": below, "filter_threshold": hwe_threshold}
               if hwe_threshold is not None else {}),
        }
        if n == 0:
            result["reason"] = "no_observed_founder_genotypes" if counts_available else "no_valid_hwe_records"
        return result

    autosomal_path = hwe_path if hwe_path is not None else Path(f"{prefix}.hardy")
    chromosome_x_path = autosomal_path.with_name(autosomal_path.name + ".x")
    hwe = {
        "autosomal": hwe_report(autosomal_path),
        "chromosome_x": hwe_report(chromosome_x_path),
        "filter_threshold": hwe_threshold,
        "scope": "founders_only",
        "available": False,
    }
    hwe["available"] = bool(hwe["autosomal"]["available"] or hwe["chromosome_x"]["available"])

    return {
        "variant_missingness": variant_missing,
        "sample_missingness": sample_missing,
        "minor_allele_frequency": maf,
        "hardy_weinberg": hwe,
    }




def stage_qc(cfg: dict, ctx: RunContext) -> dict:
    """Run QC with all-sample MAF and founder-aware HWE handling."""
    plink2, _ = _setup(ctx)
    rdir, log = ctx.rdir, ctx.log_path
    ctx.progress("input", 5, "Menyiapkan data input")
    files = V.find_genotype_files(ctx.ddir)
    vcf = files.get(".vcf.gz") or files.get(".vcf")
    if vcf:
        ctx.log("input", "Mengonversi VCF ke format PLINK (.bed/.bim/.fam)")
        run_tool(
            "konversi VCF",
            [plink2, "--vcf", str(vcf), "--double-id", "--allow-extra-chr",
             "--set-missing-var-ids", "@:#", "--make-bed", "--out", "input"],
            rdir, log,
        )

    input_prefix = _input_prefix(ctx)
    prep = _prepare_tables(cfg, ctx, input_prefix, with_covariates=False)
    input_bim = Path(f"{input_prefix}.bim")
    input_fam = Path(f"{input_prefix}.fam")
    n_variants_in = _count_lines(input_bim)
    pedigree = _pedigree_summary(input_fam, rdir / "keep.txt")
    founder_count = pedigree["founders"]
    hwe_reason = None if founder_count > 0 else "no_founders_available_for_founder_only_hwe"
    ctx.log(
        "input",
        f"{prep['n_samples']} sampel cocok; {n_variants_in} varian input; "
        f"{founder_count} founder dan {pedigree['families']} FID pada sampel analisis",
    )

    qc = cfg["qc"]
    hwe_metric_threshold = qc["hwe_p"] if (qc["hwe_p"] is not None and founder_count > 0 and not qc["skip_qc"]) else None
    metrics_before: dict = {}
    before_frequency_ok = False
    for stale in (
        # Output tahap MAF/missingness.
        rdir / "qc_maf.log", rdir / "qc_maf.bed",
        rdir / "qc_maf.bim", rdir / "qc_maf.fam",
        rdir / "qc_maf.smiss", rdir / "qc_maf.vmiss",
        rdir / "qc_maf.afreq",

        # Output QC final dari percobaan sebelumnya.
        rdir / "qc.log", rdir / "qc.bed",
        rdir / "qc.bim", rdir / "qc.fam",
        rdir / "qc.smiss", rdir / "qc.vmiss",
        rdir / "qc_summary.json",

        rdir / "qc_before_metrics.log", rdir / "qc_before_metrics.afreq",
        rdir / "qc_before_metrics.smiss", rdir / "qc_before_metrics.vmiss",
        rdir / "qc_before_hwe.log", rdir / "qc_before_hwe.hardy",
        rdir / "qc_before_hwe.hardy.x", rdir / "qc_after_metrics.log",
        rdir / "qc_after_metrics.afreq", rdir / "qc_after_hwe.log",
        rdir / "qc_after_hwe.hardy", rdir / "qc_after_hwe.hardy.x",
    ):
        stale.unlink(missing_ok=True)
    try:
        run_tool(
            "Statistik QC sebelum filter (MAF seluruh sampel)",
            [plink2, "--bfile", str(input_prefix), "--allow-extra-chr", "--keep", "keep.txt",
             "--nonfounders", "--missing", "--freq", "--out", "qc_before_metrics"],
            rdir, log,
        )
        before_frequency_ok = (rdir / "qc_before_metrics.afreq").exists()
    except (ToolError, OSError) as exc:
        metrics_before = {
            "available": False,
            "warning": f"PLINK2 gagal menghasilkan statistik sebelum QC: {type(exc).__name__}.",
        }

    hwe_before_path = rdir / "qc_before_hwe.hardy"
    hwe_before_ok = False
    if founder_count > 0:
        try:
            run_tool(
                "Statistik HWE sebelum QC (founder-only)",
                [plink2, "--bfile", str(input_prefix), "--allow-extra-chr", "--keep", "keep.txt",
                 "--hardy", "--out", "qc_before_hwe"],
                rdir, log,
            )
            hwe_before_ok = hwe_before_path.exists()
        except (ToolError, OSError) as exc:
            ctx.log("qc", f"Statistik HWE sebelum QC tidak tersedia: {type(exc).__name__}.")
    else:
        ctx.log("qc", "HWE founder-only sebelum QC dilewati karena tidak ada founder pada sampel analisis.")

    if before_frequency_ok:
        metrics_before = _summarize_plink_metrics(
            rdir / "qc_before_metrics",
            qc["call_rate"],
            hwe_metric_threshold,
            maf_path=rdir / "qc_before_metrics.afreq",
            hwe_path=hwe_before_path if hwe_before_ok else None,
            hwe_unavailable_reason=hwe_reason if not hwe_before_ok else None,
            maf_threshold=qc["maf"] if not qc["skip_qc"] else None,
        )
    elif qc["maf"] > 0 and not qc["skip_qc"]:
        raise PipelineError(
            "QC_MAF_REFERENCE_UNAVAILABLE",
            "Frekuensi alel seluruh sampel tidak berhasil dihitung; filter MAF tidak dijalankan agar tidak kembali memakai frekuensi founder-only.",
            "Periksa log qc_before_metrics.log dan pastikan PLINK2 mendukung --nonfounders.",
        )

    ctx.progress("qc", 15, "Quality Control")

    # --------------------------------------------------------
    # TAHAP A: MAF seluruh sampel + variant missingness.
    #
    # --nonfounders hanya digunakan ketika filter MAF aktif.
    # Tidak ada --read-freq, sehingga frekuensi dihitung
    # langsung dari sampel yang dipakai pada analisis ini.
    # --------------------------------------------------------

    maf_cmd = [
        plink2,
        "--bfile", str(input_prefix),
        "--allow-extra-chr",
        "--keep", "keep.txt",
        "--max-alleles", "2",
    ]

    if not qc["skip_qc"]:
        if qc["maf"] > 0:
            maf_cmd += [
                "--nonfounders",
                "--maf", f"{qc['maf']:g}",
            ]

        maf_cmd += [
            "--geno", f"{1 - qc['call_rate']:.6f}",
        ]

    run_tool(
        "Quality Control: MAF seluruh sampel dan missingness",
        maf_cmd + [
            "--make-bed",
            "--missing",
            "--out", "qc_maf",
        ],
        rdir,
        log,
    )

    # --------------------------------------------------------
    # TAHAP B: HWE founder-only.
    #
    # Tidak menggunakan --nonfounders. Jika tidak ada founder,
    # HWE dilewati sesuai rancangan GenoSphere.
    #
    # Keluaran tahap ini selalu menggunakan prefix "qc"
    # agar tahap PCA/GWAS berikutnya tetap kompatibel.
    # --------------------------------------------------------

    hwe_filter_applied = False

    hwe_cmd = [
        plink2,
        "--bfile", "qc_maf",
        "--allow-extra-chr",
        "--keep", "keep.txt",
    ]

    if (
        not qc["skip_qc"]
        and qc["hwe_p"] is not None
        and founder_count > 0
    ):
        hwe_cmd += [
            "--hwe", f"{qc['hwe_p']:g}",
        ]
        hwe_filter_applied = True

    run_tool(
        (
            "Quality Control: HWE founder-only"
            if hwe_filter_applied
            else "Menyiapkan keluaran QC final"
        ),
        hwe_cmd + [
            "--make-bed",
            "--missing",
            "--out", "qc",
        ],
        rdir,
        log,
    )

    # Generate post-QC MAF over all retained samples, independently of founder status.
    after_frequency_ok = False
    try:
        run_tool(
            "Statistik frekuensi setelah QC (seluruh sampel)",
            [plink2, "--bfile", "qc", "--allow-extra-chr", "--keep", "keep.txt",
             "--nonfounders", "--freq", "--out", "qc_after_metrics"],
            rdir, log,
        )
        after_frequency_ok = (rdir / "qc_after_metrics.afreq").exists()
    except (ToolError, OSError) as exc:
        ctx.log("qc", f"Statistik MAF setelah QC tidak tersedia: {type(exc).__name__}.")

    hwe_after_path = rdir / "qc_after_hwe.hardy"
    hwe_after_ok = False
    if founder_count > 0:
        try:
            run_tool(
                "Statistik HWE setelah QC (founder-only)",
                [plink2, "--bfile", "qc", "--allow-extra-chr", "--keep", "keep.txt",
                 "--hardy", "--out", "qc_after_hwe"],
                rdir, log,
            )
            hwe_after_ok = hwe_after_path.exists()
        except (ToolError, OSError) as exc:
            ctx.log("qc", f"Statistik HWE setelah QC tidak tersedia: {type(exc).__name__}.")

    metrics_after = _summarize_plink_metrics(
        rdir / "qc",
        qc["call_rate"],
        hwe_metric_threshold,
        maf_path=rdir / "qc_after_metrics.afreq",
        hwe_path=hwe_after_path if hwe_after_ok else None,
        hwe_unavailable_reason=hwe_reason if not hwe_after_ok else None,
        maf_threshold=qc["maf"] if not qc["skip_qc"] else None,
    )

    n_variants_qc = _count_lines(rdir / "qc.bim")
    samples_before = _count_lines(input_fam)
    samples_after = _count_lines(rdir / "qc.fam")
    # Filter MAF/missingness berada di qc_maf.log.
    # Filter HWE (jika diterapkan) berada di qc.log.
    # Gabungkan supaya laporan tetap mencakup seluruh tahapan.
    removed_by_filter: dict[str, int] = {}

    for qc_log_path in (
        rdir / "qc_maf.log",
        rdir / "qc.log",
    ):
        for filter_name, removed_count in _parse_qc_log(qc_log_path).items():
            removed_by_filter[filter_name] = (
                removed_by_filter.get(filter_name, 0) + removed_count
            )


    before_load = _parse_variant_loader_summary(rdir / "qc_before_metrics.log")
    qc_load = _parse_variant_loader_summary(rdir / "qc_maf.log")
    plink_eligible_before = before_load.get("variants_remaining", n_variants_in)
    plink_eligible_qc = qc_load.get("variants_remaining", plink_eligible_before)
    negative_bp = _count_negative_bp_variants(input_bim)
    negative_bp_observed = min(negative_bp, max(0, n_variants_in - plink_eligible_before))
    other_pre_filter_exclusions = max(
        0,
        (n_variants_in - plink_eligible_before) - negative_bp_observed,
    )
    additional_qc_loader_exclusions = max(0, plink_eligible_before - plink_eligible_qc)
    total_removed = max(0, n_variants_in - n_variants_qc)
    logged_filter_removals = sum(
        value for value in removed_by_filter.values() if isinstance(value, int) and value >= 0
    )
    accounted_removals = (
        negative_bp_observed
        + other_pre_filter_exclusions
        + additional_qc_loader_exclusions
        + logged_filter_removals
    )
    unattributed = max(0, total_removed - accounted_removals)

    input_exclusions = {
        "total_input_variants": n_variants_in,
        "variants_available_before_qc_filters": plink_eligible_before,
        "excluded_by_plink_before_qc_filters": max(0, n_variants_in - plink_eligible_before),
        "negative_bp_coordinates": negative_bp_observed,
        "other_pre_filter_loader_exclusions": other_pre_filter_exclusions,
        "additional_loader_exclusions_with_qc_options": additional_qc_loader_exclusions,
    }
    filter_breakdown = {
        "variants_removed_by_filter": removed_by_filter,
        "input_exclusions": input_exclusions,
        "total_variants_removed": total_removed,
        "unattributed_variant_removal": unattributed,
    }

    warnings = []
    if negative_bp_observed:
        warnings.append({
            "code": "NEGATIVE_BP_COORDINATES_EXCLUDED",
            "message": f"{negative_bp_observed} varian memiliki koordinat BP negatif dan dikeluarkan saat pemuatan PLINK2, sebelum filter QC utama.",
        })
    if other_pre_filter_exclusions or additional_qc_loader_exclusions:
        warnings.append({
            "code": "PLINK_INPUT_LOADER_EXCLUSIONS",
            "message": "Ada varian tambahan yang dikeluarkan saat pemuatan/opsi input PLINK2; lihat input_exclusions untuk jumlahnya.",
        })
    if unattributed:
        warnings.append({
            "code": "QC_REMOVAL_UNATTRIBUTED",
            "message": f"{unattributed} pengurangan varian belum dapat diatribusikan ke log filter atau eksklusi input yang tercatat.",
        })
    if not metrics_before.get("variant_missingness", {}).get("available"):
        warnings.append({"code": "MISSINGNESS_BEFORE_UNAVAILABLE", "message": "Statistik missingness sebelum QC tidak tersedia."})
    if not metrics_after.get("variant_missingness", {}).get("available"):
        warnings.append({"code": "MISSINGNESS_AFTER_UNAVAILABLE", "message": "Statistik missingness setelah QC tidak tersedia."})
    if founder_count == 0 and qc["hwe_p"] is not None and not qc["skip_qc"]:
        warnings.append({
            "code": "HWE_FILTER_SKIPPED_NO_FOUNDERS",
            "message": "Filter HWE tidak diterapkan karena tidak ada founder; statistik HWE seluruh anggota keluarga tidak digunakan sebagai filter independen.",
        })
    elif founder_count > 0 and qc["hwe_p"] is not None and not hwe_filter_applied:
        warnings.append({"code": "HWE_FILTER_NOT_APPLIED", "message": "Ambang HWE dikonfigurasi tetapi filter HWE tidak berhasil diterapkan."})
    if not metrics_before.get("hardy_weinberg", {}).get("available"):
        warnings.append({"code": "HWE_BEFORE_UNAVAILABLE", "message": "Statistik HWE founder-only sebelum QC tidak tersedia."})
    if not metrics_after.get("hardy_weinberg", {}).get("available"):
        warnings.append({"code": "HWE_AFTER_UNAVAILABLE", "message": "Statistik HWE founder-only setelah QC tidak tersedia."})
    if not metrics_after.get("minor_allele_frequency", {}).get("available"):
        warnings.append({"code": "MAF_AFTER_UNAVAILABLE", "message": "Statistik MAF setelah QC tidak tersedia."})
    if qc["skip_qc"]:
        warnings.append({"code": "QC_SKIPPED", "message": "QC dilewati sesuai konfigurasi; filter MAF, missingness, dan HWE tidak diterapkan."})

    qc_summary = {
        "skipped": qc["skip_qc"],
        "parameters": qc,
        "filter_parameters": {
            "skip_qc": qc["skip_qc"],
            "maf": qc["maf"] if not qc["skip_qc"] else None,
            "call_rate": qc["call_rate"] if not qc["skip_qc"] else None,
            "hwe_p": qc["hwe_p"] if hwe_filter_applied else None,
        },
        "effective_filter_parameters": {
            "maf": qc["maf"] if not qc["skip_qc"] else None,
            "call_rate": qc["call_rate"] if not qc["skip_qc"] else None,
            "hwe_p": qc["hwe_p"] if hwe_filter_applied else None,
        },
        "pedigree": pedigree,
        "variants_before": n_variants_in,
        "variants_after": n_variants_qc,
        "variants_removed": n_variants_in - n_variants_qc,
        "variants_removed_percent": round(100 * (n_variants_in - n_variants_qc) / n_variants_in, 4) if n_variants_in else 0.0,
        "input_exclusions": input_exclusions,
        "samples_before": samples_before,
        "samples_after": samples_after,
        "samples_removed": samples_before - samples_after,
        "sample_accounting_pre_qc": prep.get("sample_accounting", {}),
        "samples_removed_percent": round(100 * (samples_before - samples_after) / samples_before, 4) if samples_before else 0.0,
        "removed_by_filter": removed_by_filter,
        "filter_breakdown": filter_breakdown,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "warnings": warnings,
    }
    (rdir / "qc_summary.json").write_text(json.dumps(qc_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log(
        "qc",
        f"{n_variants_qc} dari {n_variants_in} varian input tersedia setelah QC; "
        f"{qc_summary['samples_after']} sampel dipakai; filter={removed_by_filter}",
    )
    if n_variants_qc == 0:
        raise PipelineError(
            "NO_VARIANTS_AFTER_QC",
            "Tidak ada varian yang lolos QC.",
            "Longgarkan ambang MAF/call rate atau periksa kualitas data.",
        )
    return qc_summary




def _compute_pca_python(rdir_path: Path, max_k: int) -> bool:
    """Fallback PCA hemat memori memakai matriks Gram sampel x sampel.

    Genotipe dibaca per varian dari BED, bukan dimaterialisasi sebagai matriks
    varian x sampel penuh. Hasil PC ekuivalen dengan left singular vectors dari
    matriks genotipe yang sudah diimputasi, dipusatkan, dan distandardisasi.
    """
    fam_path, bim_path, bed_path = (rdir_path / "qc.fam", rdir_path / "qc.bim", rdir_path / "qc.bed")
    if not all(p.exists() for p in (fam_path, bim_path, bed_path)):
        return False
    samples = []
    with fam_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 2:
                samples.append((parts[0], parts[1]))
    n_samples, n_variants = len(samples), _count_lines(bim_path)
    if n_samples < 2 or n_variants < 1:
        return False
    allowed, estimated_bytes, budget_bytes = check_pca_fallback_memory(n_samples, n_variants)
    if not allowed:
        raise PipelineError("PCA_MEMORY_LIMIT",
            f"Fallback PCA memerlukan estimasi {estimated_bytes / (1024**2):.1f} MiB untuk matriks Gram, melebihi anggaran {budget_bytes / (1024**2):.1f} MiB.",
            "PLINK2 PCA gagal dan fallback Python dihentikan untuk mencegah kehabisan memori. Tambah resource server atau kurangi ukuran dataset.")

    gram = np.zeros((n_samples, n_samples), dtype=np.float64)
    bytes_per_variant = (n_samples + 3) // 4
    n_used = 0
    with bed_path.open("rb") as handle:
        if handle.read(3) != b"\x6c\x1b\x01":
            return False
        for _ in range(n_variants):
            raw = handle.read(bytes_per_variant)
            if len(raw) != bytes_per_variant:
                return False
            dosage = np.empty(n_samples, dtype=np.float64)
            idx = 0
            for byte in raw:
                for shift in (0, 2, 4, 6):
                    if idx >= n_samples:
                        break
                    code = (byte >> shift) & 0b11
                    dosage[idx] = np.nan if code == 1 else (0.0 if code == 0 else (1.0 if code == 2 else 2.0))
                    idx += 1
            valid = np.isfinite(dosage)
            if not valid.any():
                continue
            mean = float(np.mean(dosage[valid]))
            dosage[~valid] = mean
            centered = dosage - mean
            sd = float(np.sqrt(np.mean(centered * centered)))
            if not np.isfinite(sd) or sd <= 1e-12:
                continue
            centered /= sd
            gram += np.outer(centered, centered)
            n_used += 1
    if n_used < 1:
        return False

    eigenvalues_all, vectors = np.linalg.eigh(gram)
    order = np.argsort(eigenvalues_all)[::-1]
    eigenvalues_all = np.maximum(eigenvalues_all[order], 0.0)
    vectors = vectors[:, order]
    positive = int(np.count_nonzero(eigenvalues_all > max(1e-12, eigenvalues_all[0] * 1e-12)))
    k = min(max(1, max_k), n_samples - 1, n_used, positive)
    if k < 1:
        return False
    pcs = vectors[:, :k]
    eigenvalues = eigenvalues_all[:k] / max(1, n_samples - 1)
    total = float(np.sum(eigenvalues_all) / max(1, n_samples - 1))
    variance = eigenvalues / total if total > 0 else np.zeros(k)

    with (rdir_path / "pca.eigenvec").open("w", encoding="utf-8") as handle:
        handle.write("#FID\tIID\t" + "\t".join(f"PC{i+1}" for i in range(k)) + "\n")
        for idx, (fid, iid) in enumerate(samples):
            handle.write(f"{fid}\t{iid}\t" + "\t".join(f"{pcs[idx, c]:.10g}" for c in range(k)) + "\n")
    (rdir_path / "pca.eigenval").write_text("\n".join(f"{x:.12g}" for x in eigenvalues) + "\n", encoding="utf-8")
    (rdir_path / "pca.variance.json").write_text(json.dumps({
        "variance_explained": variance.tolist(),
        "cumulative_variance_explained": np.cumsum(variance).tolist(),
        "total_variance": total,
        "variants_used": n_used,
    }, indent=2), encoding="utf-8")
    return True


def stage_pca(cfg: dict, ctx: RunContext) -> dict:
    """Run PCA with founder LD or a deterministic one-sample-per-FID reference."""
    _require_qc(ctx)
    rdir, log = ctx.rdir, ctx.log_path

    # Remove only PCA outputs/intermediates from a previous attempt in this run directory.
    for old in rdir.glob("pca.*"):
        old.unlink(missing_ok=True)
    for prefix, suffixes in {
        "pca_input": (".log", ".pgen", ".pvar", ".psam"),
        "pca_prune": (".log", ".prune.in", ".prune.out"),
        "pca_reference": (".keep",),
        "pca_reference_freq": (".log", ".afreq"),
    }.items():
        for suffix in suffixes:
            (rdir / f"{prefix}{suffix}").unlink(missing_ok=True)

    if not cfg.get("use_pca"):
        ctx.log("pca", "PCA tidak dipakai pada analisis ini")
        return {"status": "not_used", "requested_components": 0, "actual_components": 0, "warnings": []}

    plink2, _ = _setup(ctx)
    qc_bim = rdir / "qc.bim"
    qc_fam = rdir / "qc.fam"
    n_variants = _count_lines(qc_bim) if qc_bim.exists() else 0
    n_samples = _count_lines(qc_fam) if qc_fam.exists() else 0
    requested = int(cfg.get("pca_components") or 5)
    warnings: list[str] = []
    if requested not in (5, 10, 20):
        raise PipelineError("PCA_INVALID_REQUEST", "Pilihan PCA harus 5, 10, atau 20 komponen.", "Ubah konfigurasi PCA lalu coba lagi.")

    possible = min(requested, max(0, n_samples - 1), n_variants)
    if possible < requested:
        warnings.append(f"Data secara dimensi hanya memungkinkan maksimal {possible} PC dari {requested} yang diminta.")
    pedigree = _pedigree_summary(qc_fam, rdir / "keep.txt")
    founder_count = pedigree["founders"]
    family_count = pedigree["families"]
    reference_samples = 0
    pruned_variants = 0
    method = "python_fallback"
    produced = False
    ctx.progress("pca", 40, f"Principal Component Analysis (maksimal {possible} komponen)")

    if possible > 0 and n_variants > 50:
        try:
            run_tool(
                "Persiapan PGEN PCA terurut",
                [plink2, "--bfile", "qc", "--allow-extra-chr", "--sort-vars", "--make-pgen", "--out", "pca_input"],
                rdir, log,
            )
            if not all((rdir / f"pca_input{suffix}").exists() for suffix in (".pgen", ".pvar", ".psam")):
                raise RuntimeError("PLINK2 tidak menghasilkan berkas PGEN PCA yang lengkap.")

            prune_file = rdir / "pca_prune.prune.in"
            if founder_count >= 50:
                run_tool(
                    "LD pruning founder",
                    [plink2, "--pfile", "pca_input", "--allow-extra-chr", "--keep", "keep.txt",
                     "--indep-pairwise", "50", "5", "0.2", "--out", "pca_prune"],
                    rdir, log,
                )
                method = "plink2_founder_ld"
            elif family_count >= 50:
                reference_keep = rdir / "pca_reference.keep"
                reference_samples = _write_family_reference_keep(
                    qc_fam,
                    rdir / "keep.txt",
                    reference_keep,
                    minimum_families=50,
                )
                if reference_samples < 50:
                    raise RuntimeError("Referensi satu sampel per FID kurang dari 50 sampel.")

                run_tool(
                    "LD pruning referensi satu sampel per FID",
                    [plink2, "--pfile", "pca_input", "--allow-extra-chr", "--keep", "pca_reference.keep",
                     "--make-founders", "--indep-pairwise", "50", "5", "0.2", "--out", "pca_prune"],
                    rdir, log,
                )
                method = "plink2_family_reference"
            else:
                raise RuntimeError(
                    f"Founder hanya {founder_count} dan FID hanya {family_count}; "
                    "tidak cukup untuk LD pruning PLINK2 yang aman."
                )

            if not prune_file.exists():
                raise RuntimeError("PLINK2 tidak menghasilkan pca_prune.prune.in.")
            pruned_variants = _count_lines(prune_file)
            if pruned_variants < 1:
                raise RuntimeError("LD pruning tidak mempertahankan varian.")

            pca_cmd = [
                plink2, "--pfile", "pca_input", "--allow-extra-chr",
                "--keep", "keep.txt", "--extract", "pca_prune.prune.in",
            ]
            if method == "plink2_family_reference":
                # Frequencies are calculated from exactly the pruned variants and reference samples.
                run_tool(
                    "Frekuensi alel referensi PCA",
                    [plink2, "--pfile", "pca_input", "--allow-extra-chr", "--keep", "pca_reference.keep",
                     "--make-founders", "--extract", "pca_prune.prune.in", "--freq", "--out", "pca_reference_freq"],
                    rdir, log,
                )
                if not (rdir / "pca_reference_freq.afreq").exists():
                    raise RuntimeError("Frekuensi alel referensi PCA tidak terbentuk.")
                pca_cmd += ["--read-freq", "pca_reference_freq.afreq"]
                warnings.append(
                    "LD pruning dan frekuensi alel menggunakan satu sampel per FID sebagai referensi. "
                    "Ini merupakan pendekatan komputasional; satu sampel per FID tidak membuktikan "
                    "bahwa semua keluarga tidak berkerabat secara biologis."
                )

            pca_cmd += ["--pca", str(possible), "--out", "pca"]
            run_tool("PCA PLINK2", pca_cmd, rdir, log)
            produced = (rdir / "pca.eigenvec").exists() and (rdir / "pca.eigenval").exists()
            if not produced:
                raise RuntimeError("Output eigenvector/eigenvalue PCA tidak lengkap.")

        except Exception as exc:
            warnings.append(f"PCA PLINK2 tidak berhasil; sistem mencoba fallback Python: {type(exc).__name__}: {exc}")
            ctx.log("pca", f"PLINK2 PCA gagal ({type(exc).__name__}); mencoba fallback PCA Python.")
            produced = False
            method = "python_fallback"

    if not produced and possible > 0:
        try:
            produced = _compute_pca_python(rdir, possible)
            if produced:
                method = "python_fallback"
        except PipelineError as exc:
            warnings.append(exc.message)
            ctx.log("pca", f"Fallback PCA tidak dijalankan: {exc.code} {exc.message}")
        except Exception as exc:
            warnings.append(f"Fallback PCA gagal: {type(exc).__name__}.")

    actual = 0
    components: list[str] = []
    eigenvalues: list[float] = []
    variance: list[float] = []
    cumulative: list[float] = []
    samples_used = 0
    eigenvec = rdir / "pca.eigenvec"
    if produced and eigenvec.exists():
        try:
            with eigenvec.open("r", encoding="utf-8") as handle:
                header = handle.readline().lstrip("#").split()
                components = header[2:]
                actual = len(components)
                samples_used = sum(1 for line in handle if line.strip())
            if (rdir / "pca.eigenval").exists():
                eigenvalues = [float(value) for value in (rdir / "pca.eigenval").read_text().split() if _float(value) is not None]
            variance_file = rdir / "pca.variance.json"
            if variance_file.exists():
                variance_data = json.loads(variance_file.read_text(encoding="utf-8"))
                variance = variance_data.get("variance_explained", [])
                cumulative = variance_data.get("cumulative_variance_explained", [])
            elif eigenvalues:
                warnings.append("Variance explained tidak dihitung karena eigenvalues total seluruh ruang PCA tidak tersedia dari output PLINK2.")
            if samples_used != n_samples:
                warnings.append(f"Jumlah sampel pada output PCA ({samples_used}) berbeda dari sampel QC ({n_samples}).")
        except (OSError, ValueError, IndexError, json.JSONDecodeError) as exc:
            warnings.append(f"Output PCA tidak dapat dibaca dengan konsisten: {type(exc).__name__}.")
            actual = 0
            components = []

    if actual >= requested:
        status = "complete"
    elif actual > 0:
        status = "partial"
        warnings.append(f"PCA menghasilkan {actual} dari {requested} PC yang diminta; hanya komponen aktual yang akan digunakan sebagai kovariat.")
    else:
        status = "unavailable"
        warnings.append("Tidak ada principal component yang berhasil dihasilkan. Analisis asosiasi dengan koreksi PCA tidak akan dilanjutkan.")

    summary = {
        "requested_components": requested,
        "actual_components": actual,
        "status": status,
        "method": method,
        "founder_count": founder_count,
        "family_count": family_count,
        "reference_samples": reference_samples,
        "ld_pruned_variants": pruned_variants,
        "warnings": warnings,
        "samples_used": samples_used,
        "variants_after_qc": n_variants,
        "eigenvalues": eigenvalues,
        "variance_explained": variance,
        "cumulative_variance_explained": cumulative,
    }
    (rdir / "pca_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log("pca", f"PCA {status}: {actual}/{requested} PC; {samples_used} sampel dipakai; metode={method}.")
    return summary




def stage_gwas(cfg: dict, ctx: RunContext) -> dict:
    _require_qc(ctx)
    s = get_settings()
    plink2, versions = _setup(ctx)
    rdir, log = ctx.rdir, ctx.log_path
    qc_summary = json.loads((rdir / "qc_summary.json").read_text(encoding="utf-8"))
    prep = _prepare_tables(cfg, ctx, _input_prefix(ctx))
    for old in list(rdir.glob("gwas.*")) + list(rdir.glob("qc_pheno.*")) + [rdir / "gemma_covar.txt"]:
        old.unlink(missing_ok=True)

    pcs, pc_names = None, []
    pca_summary = {}
    if cfg["use_pca"]:
        summary_path = rdir / "pca_summary.json"
        if not summary_path.exists():
            raise PipelineError("STAGE_ORDER", "PCA dipilih tetapi tahap PCA belum dijalankan.", "Jalankan tahap PCA terlebih dahulu.")
        pca_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if pca_summary.get("status") == "unavailable" or not (rdir / "pca.eigenvec").exists():
            raise PipelineError("PCA_UNAVAILABLE", "PCA tidak menghasilkan komponen yang dapat digunakan; analisis dihentikan agar koreksi struktur populasi tidak dilewati diam-diam.",
                                "Periksa warning PCA, kualitas data, atau kapasitas server. Nonaktifkan PCA hanya bila secara metodologis memang sesuai.")
        pcs, pc_names = _read_eigenvec(rdir / "pca.eigenvec")
        if not pc_names:
            raise PipelineError("PCA_UNAVAILABLE", "Output PCA tidak berisi komponen yang dapat digunakan.", "Periksa kembali hasil PCA.")
    covar = _write_covar(ctx, prep, pcs, pc_names)

    model = cfg["model"]
    ctx.progress("gwas", 55, f"Analisis asosiasi: {MODEL_LABEL[model]}")
    if model == "lmm":
        gemma = resolve_binary(s.gemma_bin)
        versions["gemma"] = tool_version(gemma, ["-h"])
        results = _run_gemma(plink2, gemma, covar, prep, ctx)
        parser = iter_gemma_results
    else:
        glm = [plink2, "--bfile", "qc", "--allow-extra-chr", "--keep", "keep.txt", "--pheno", "pheno.txt", "--pheno-name", "TRAIT"]
        if covar:
            glm += ["--covar", str(covar), "--covar-variance-standardize", "--glm", "hide-covar"]
        else:
            glm += ["--glm", "allow-no-covars"]
        run_tool(MODEL_LABEL[model], glm + ["--out", "gwas"], rdir, log)
        found = sorted(rdir.glob("gwas.TRAIT.glm.*"))
        if not found:
            raise PipelineError("NO_RESULT_FILE", "PLINK tidak menghasilkan berkas hasil asosiasi.", "Periksa log analisis.")
        results, parser = found[0], iter_plink_results
    ctx.log("gwas", f"Hasil mentah: {results.name}")
    return {"results_file": results, "parser": parser, "versions": versions, "model": model,
            "n_samples": prep["n_samples"], "n_variants_tested_input": qc_summary["variants_after"], "qc_summary": qc_summary,
            "pca_summary": pca_summary, "sample_accounting": prep.get("sample_accounting", {})}


def run_pipeline(cfg: dict, ctx: RunContext, stages: tuple[str, ...] = STAGES) -> dict:
    result: dict = {}
    for st in stages:
        if st == "qc":
            result["qc_summary"] = stage_qc(cfg, ctx)
        elif st == "pca":
            stage_pca(cfg, ctx)
        elif st == "gwas":
            result = stage_gwas(cfg, ctx)
    return result


def _run_gemma(plink2: str, gemma: str, covar: Path | None, prep: dict, ctx: RunContext) -> Path:
    rdir, log = ctx.rdir, ctx.log_path
    (rdir / "gemma").mkdir(exist_ok=True)
    run_tool("persiapan GEMMA", [plink2, "--bfile", "qc", "--allow-extra-chr", "--keep", "keep.txt", "--pheno", "pheno.txt", "--pheno-name", "TRAIT",
                                  "--make-bed", "--out", "qc_pheno"], rdir, log)
    run_tool("GEMMA kinship", [gemma, "-bfile", "qc_pheno", "-gk", "1", "-outdir", "gemma", "-o", "kin"], rdir, log)
    cmd = [gemma, "-bfile", "qc_pheno", "-k", "gemma/kin.cXX.txt", "-lmm", "4", "-outdir", "gemma", "-o", "lmm"]
    if covar:
        rows = {l.split("\t")[1]: l.rstrip("\n").split("\t")[2:] for l in open(covar, encoding="utf-8").read().splitlines()[1:]}
        order = [l.split()[1] for l in open(rdir / "qc_pheno.fam", encoding="utf-8")]
        with open(rdir / "gemma_covar.txt", "w", encoding="utf-8") as f:
            for iid in order:
                f.write("\t".join(["1"] + rows[iid]) + "\n")
        cmd += ["-c", "gemma_covar.txt"]
    run_tool("GEMMA LMM", cmd, rdir, log)
    out = rdir / "gemma" / "lmm.assoc.txt"
    if not out.exists():
        raise PipelineError("NO_RESULT_FILE", "GEMMA tidak menghasilkan berkas hasil asosiasi.", "Periksa log analisis.")
    return out


def _read_eigenvec(path: Path) -> tuple[dict, list[str]]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    names = lines[0].lstrip("#").split()[2:]
    return {l.split()[1]: l.split()[2:] for l in lines[1:]}, names


def _parse_qc_log(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            m = re.match(r"--(geno|hwe): ([\d,]+) variants? removed", line)
            if m:
                out[m.group(1)] = int(m.group(2).replace(",", ""))
            m = re.match(r"([\d,]+) variants? removed due to allele frequency", line)
            if m:
                out["maf"] = int(m.group(1).replace(",", ""))
    except OSError:
        pass
    return out


def _float(v: str) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def iter_plink_results(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        header = f.readline().lstrip("#").rstrip("\n").split("\t")
        ix = {h: i for i, h in enumerate(header)}
        effect_col = "BETA" if "BETA" in ix else "OR"
        for line in f:
            c = line.rstrip("\n").split("\t")
            if "TEST" in ix and c[ix["TEST"]] != "ADD":
                continue
            p, eff = _float(c[ix["P"]]), _float(c[ix[effect_col]])
            if p is None or eff is None:
                continue
            if effect_col == "OR":
                if eff <= 0:
                    continue
                eff = math.log(eff)
            yield {"rsid": c[ix["ID"]], "chrom": c[ix["CHROM"]], "pos": int(c[ix["POS"]]),
                   "ref": c[ix["REF"]], "alt": c[ix["ALT"]], "effect": eff, "p": p}


def iter_gemma_results(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        header = f.readline().split()
        ix = {h: i for i, h in enumerate(header)}
        for line in f:
            c = line.split()
            p, eff = _float(c[ix["p_wald"]]), _float(c[ix["beta"]])
            if p is None or eff is None:
                continue
            yield {"rsid": c[ix["rs"]], "chrom": c[ix["chr"]], "pos": int(c[ix["ps"]]),
                   "ref": c[ix["allele0"]], "alt": c[ix["allele1"]], "effect": eff, "p": p}


def _ensure_float_columns(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    rows = db.execute(text("SELECT column_name, data_type FROM information_schema.columns "
                           "WHERE table_name='gwas_result' AND column_name IN ('p_value','effect_size')")).all()
    if any(dtype == "numeric" for _, dtype in rows):
        raise PipelineError("DB_SCHEMA_PRECISION",
                            "Kolom gwas_result.p_value dan effect_size bertipe NUMERIC(15,10), p-value yang sangat kecil akan dibulatkan menjadi 0.",
                            "Buat ulang tabel dengan sql/schema.sql (kolom bertipe DOUBLE PRECISION), lalu ulangi analisis.")


def import_results(db: Session, analysis_id: int, rows: Iterator[dict],
                   on_progress: Callable[[int], None] | None = None) -> dict:
    _ensure_float_columns(db)
    total = 0
    chunk: list[dict] = []

    def flush():
        nonlocal total
        if not chunk:
            return
        by_chrom: dict[str, list[dict]] = {}
        for r in chunk:
            by_chrom.setdefault(r["chrom"][:50], []).append(r)
        key = lambda chrom, r: (chrom, r["pos"], r["ref"][:10], r["alt"][:10])  # noqa: E731
        cache: dict[tuple, int] = {}
        for chrom, items in by_chrom.items():
            positions = list({i["pos"] for i in items})
            for sid, pos, ref, alt in db.execute(select(Snp.snp_id, Snp.position, Snp.ref_allele, Snp.alt_allele)
                                                 .where(Snp.chromosome == chrom, Snp.position.in_(positions))):
                cache[(chrom, pos, ref, alt)] = sid
        new = {}
        for chrom, items in by_chrom.items():
            for r in items:
                k = key(chrom, r)
                if k not in cache and k not in new:
                    new[k] = {"rsid": r["rsid"][:100], "chromosome": chrom, "position": r["pos"], "ref_allele": k[2], "alt_allele": k[3]}
        if new:
            for sid, chrom, pos, ref, alt in db.execute(
                    insert(Snp).returning(Snp.snp_id, Snp.chromosome, Snp.position, Snp.ref_allele, Snp.alt_allele), list(new.values())):
                cache[(chrom, pos, ref, alt)] = sid
        payload = []
        for chrom, items in by_chrom.items():
            for r in items:
                p = r["p"] if r["p"] > 0 else P_FLOOR
                payload.append({"analysis_id": analysis_id, "snp_id": cache[key(chrom, r)], "p_value": p,
                                "effect_size": r["effect"], "is_significant": False})
        db.execute(insert(GwasResult), payload)
        db.commit()
        total += len(payload)
        chunk.clear()
        if on_progress:
            on_progress(total)

    for row in rows:
        chunk.append(row)
        if len(chunk) >= CHUNK:
            flush()
    flush()
    return {"n_results": total}