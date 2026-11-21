"""Pembuat data kecil untuk tes unggah dan validasi."""
import math

IDS = [f"S{i:02d}" for i in range(12)]   # 12 sampel: di atas batas minimum 10


def fam(ids=IDS) -> bytes:
    return "".join(f"{i} {i} 0 0 0 -9\n" for i in ids).encode()


def bim(n=4) -> bytes:
    return "".join(f"1\trs{k}\t0\t{1000 + k}\tA\tG\n" for k in range(n)).encode()


def bed(n_samples=12, n_variants=4, missing_in_variant=None) -> bytes:
    """Berkas .bed sintetis. Bila missing_in_variant diisi, sampel pertama pada varian itu berkode 01 (data hilang)."""
    per = math.ceil(n_samples / 4)
    body = bytearray(n_variants * per)
    if missing_in_variant is not None:
        body[missing_in_variant * per] = 0b00000001
    return b"\x6c\x1b\x01" + bytes(body)


def pheno_csv(ids=IDS, header="FID,IID,PHENO_HEIGHT,PHENO_DISEASE,COV_AGE,COV_SEX", rows=None) -> bytes:
    if rows is None:
        rows = [f"{i},{i},{150 + n},{n % 2},{30 + n},{'L' if n % 2 else 'P'}" for n, i in enumerate(ids)]
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def plink_files(ids=IDS, n_variants=4, **kw):
    return [("g.bed", bed(len(ids), n_variants, **kw)), ("g.bim", bim(n_variants)), ("g.fam", fam(ids))]
