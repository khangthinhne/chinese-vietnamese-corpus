"""Embedding câu cho aligner bertalign: nạp mô hình kiểu sentence-transformers bằng transformers thuần,
fp16 trên GPU, cache theo hash câu (SQLite, một file cho mỗi mô hình + max_length).

Không phụ thuộc gói sentence-transformers: đọc modules.json của mô hình và dựng lại chuỗi
Transformer -> Pooling (cls|mean) -> Dense (tuỳ chọn) -> Normalize. Hai mô hình đã kiểm:
    labse   sentence-transformers/LaBSE   CLS -> Dense 768 tanh -> Normalize
    bge-m3  BAAI/bge-m3                   CLS -> Normalize (vector dense của M3)

Mô hình chỉ có pytorch_model.bin (bge-m3) mà torch < 2.6: transformers >= 4.50 từ chối torch.load,
nên ta tự nạp state_dict với weights_only=True (an toàn: không unpickle mã tuỳ ý).

Tìm mô hình theo thứ tự: đường dẫn thư mục -> $HAN_VIET_MODEL_DIR/<tên cuối> -> cache Hugging Face
(huggingface_hub.snapshot_download; mạng lỗi thì báo rõ).

GPU: mặc định từ chối chạy nếu GPU đã có tiến trình khác chiếm > gpu_busy_mb (quy ước dự án: không chạy hai
mô hình GPU cùng lúc).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path

import numpy as np

log = logging.getLogger("embeddings")

MODEL_ALIASES = {
    "labse": "sentence-transformers/LaBSE",
    "bge-m3": "BAAI/bge-m3",
}


def default_cache_dir() -> Path:
    return Path(os.environ.get("HAN_VIET_EMB_CACHE") or Path.home() / ".cache" / "han_viet_corpus" / "embeddings")


def resolve_model_path(model: str) -> Path:
    repo = MODEL_ALIASES.get(model.lower(), model)
    p = Path(repo)
    if p.is_dir():
        return p
    base = os.environ.get("HAN_VIET_MODEL_DIR")
    if base:
        for name in (repo.split("/")[-1], model):
            cand = Path(base) / name
            if cand.is_dir():
                return cand
    try:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(repo, allow_patterns=[
            "*.json", "*.txt", "*.model", "model.safetensors", "pytorch_model.bin",
            "1_Pooling/*", "2_Dense/*"]))
    except Exception as exc:  # mạng/SSL/không có repo
        raise ValueError(f"Không tìm được mô hình {model!r} ({repo}): {exc}. Đặt HAN_VIET_MODEL_DIR hoặc truyền "
                         "--opt model=<thư mục đã tải>") from exc


def _load_state_dict(path: Path):
    import torch
    st = path / "model.safetensors"
    if st.exists():
        from safetensors.torch import load_file
        return load_file(str(st))
    bin_ = path / "pytorch_model.bin"
    if bin_.exists():
        return torch.load(str(bin_), map_location="cpu", weights_only=True)
    raise ValueError(f"Thiếu trọng số (model.safetensors | pytorch_model.bin) trong {path}")


def _activation(name: str):
    import torch
    name = (name or "").rsplit(".", 1)[-1]
    return {"Tanh": torch.nn.Tanh(), "Identity": torch.nn.Identity(), "": torch.nn.Identity()}.get(name) \
        or getattr(torch.nn, name)()


class SentenceEncoder:
    """Bộ mã hoá câu tối thiểu, tương thích cấu trúc thư mục sentence-transformers."""

    def __init__(self, model: str, device: str = "auto", fp16: bool = True, max_length: int = 256,
                 batch_size: int = 32, gpu_busy_mb: int = 1024):
        import torch
        from transformers import AutoConfig, AutoModel, AutoTokenizer
        self.path = resolve_model_path(model)
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device.startswith("cuda") and gpu_busy_mb >= 0:
            free, total = torch.cuda.mem_get_info()
            used = (total - free - torch.cuda.memory_reserved()) / 2**20   # trừ phần chính tiến trình này giữ
            if used > gpu_busy_mb:
                raise RuntimeError(f"GPU đang bị chiếm {used:.0f} MiB (> {gpu_busy_mb}): có mô hình khác đang chạy? "
                                   "Không chạy hai mô hình GPU cùng lúc (đặt gpu_busy_mb=-1 để bỏ kiểm tra)")
        self.device, self.max_length, self.batch_size = device, max_length, batch_size
        self.dtype = torch.float16 if (fp16 and device.startswith("cuda")) else torch.float32
        modules = [{"type": "Transformer", "path": ""}, {"type": "Pooling", "path": "1_Pooling"},
                   {"type": "Normalize", "path": ""}]
        if (self.path / "modules.json").exists():
            modules = json.loads((self.path / "modules.json").read_text(encoding="utf-8"))
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.path))
        cfg = AutoConfig.from_pretrained(str(self.path))
        net = AutoModel.from_config(cfg, add_pooling_layer=False) if cfg.model_type in ("bert", "xlm-roberta") \
            else AutoModel.from_config(cfg)
        sd = _load_state_dict(self.path)
        prefix = cfg.model_type.replace("-", "") + "."         # "bert." / "xlmroberta." nếu lưu từ *ForXxx
        sd = {(k[len(prefix):] if k.startswith(prefix) else k): v for k, v in sd.items()}
        sd = {k: v for k, v in sd.items() if not k.startswith("pooler.")}
        missing, unexpected = net.load_state_dict(sd, strict=False)
        missing = [k for k in missing if "position_ids" not in k]
        if missing:
            raise ValueError(f"{self.path}: thiếu trọng số {missing[:5]}…")
        if unexpected:
            log.debug("Bỏ qua trọng số thừa: %s", unexpected[:5])
        self.net = net.to(device=device, dtype=self.dtype).eval()
        self.pooling, self.dense, self.normalize = "cls", None, False
        for m in modules:
            kind, sub = m.get("type", "").rsplit(".", 1)[-1], self.path / m.get("path", "")
            if kind == "Pooling":
                pc = json.loads((sub / "config.json").read_text(encoding="utf-8")) if (sub / "config.json").exists() else {}
                self.pooling = "mean" if pc.get("pooling_mode_mean_tokens") else "cls"
            elif kind == "Dense":
                dc = json.loads((sub / "config.json").read_text(encoding="utf-8"))
                lin = torch.nn.Linear(dc["in_features"], dc["out_features"], bias=dc.get("bias", True))
                dsd = _load_state_dict(sub)
                lin.load_state_dict({k.replace("linear.", ""): v for k, v in dsd.items()})
                self.dense = torch.nn.Sequential(lin, _activation(dc.get("activation_function"))).to(
                    device=device, dtype=self.dtype).eval()
            elif kind == "Normalize":
                self.normalize = True
        log.info("Nạp %s (%s, %s, pooling=%s, dense=%s)", self.path, device, self.dtype, self.pooling,
                 self.dense is not None)

    def encode(self, texts: list[str]) -> np.ndarray:
        import torch
        out = []
        with torch.inference_mode():
            for k in range(0, len(texts), self.batch_size):
                batch = self.tokenizer(texts[k:k + self.batch_size], padding=True, truncation=True,
                                       max_length=self.max_length, return_tensors="pt").to(self.device)
                hidden = self.net(**batch).last_hidden_state
                if self.pooling == "mean":
                    mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                    vec = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                else:
                    vec = hidden[:, 0]
                if self.dense is not None:
                    vec = self.dense(vec)
                vec = vec.float()
                vec = torch.nn.functional.normalize(vec, dim=-1)   # luôn chuẩn hoá: cosine = tích vô hướng
                out.append(vec.cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, 1), dtype=np.float32)


class EmbeddingCache:
    """SQLite: khoá sha1(văn bản) -> vector float16. Một file cho mỗi slug mô hình."""

    def __init__(self, directory: Path, slug: str):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{slug}.sqlite"
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("CREATE TABLE IF NOT EXISTS emb (h TEXT PRIMARY KEY, v BLOB)")
        self.hits = self.misses = 0

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def get_many(self, keys: list[str]) -> dict[str, np.ndarray]:
        found: dict[str, np.ndarray] = {}
        for k in range(0, len(keys), 500):
            chunk = keys[k:k + 500]
            q = f"SELECT h, v FROM emb WHERE h IN ({','.join('?' * len(chunk))})"
            for h, v in self.db.execute(q, chunk):
                found[h] = np.frombuffer(v, dtype=np.float16).astype(np.float32)
        return found

    def put_many(self, items: dict[str, np.ndarray]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO emb VALUES (?, ?)",
                            [(h, v.astype(np.float16).tobytes()) for h, v in items.items()])
        self.db.commit()


class CachedEncoder:
    """Mã hoá có cache; mô hình chỉ được nạp khi gặp câu chưa có trong cache."""

    def __init__(self, model: str, cache_dir: str | Path = "", **enc_opts):
        self.model, self.enc_opts = model, enc_opts
        self._encoder: SentenceEncoder | None = None
        name = Path(MODEL_ALIASES.get(model.lower(), model)).name
        self.slug = f"{name}_ml{enc_opts.get('max_length', 256)}"
        self.cache = EmbeddingCache(Path(cache_dir) if cache_dir else default_cache_dir(), self.slug)

    @property
    def encoder(self) -> SentenceEncoder:
        if self._encoder is None:
            self._encoder = SentenceEncoder(self.model, **self.enc_opts)
        return self._encoder

    def encode(self, texts: list[str]) -> np.ndarray:
        keys = [EmbeddingCache.key(t) for t in texts]
        found = self.cache.get_many(list(dict.fromkeys(keys)))
        todo = list(dict.fromkeys(t for t, k in zip(texts, keys) if k not in found))
        self.cache.hits += len(texts) - sum(1 for k in keys if k not in found)
        if todo:
            self.cache.misses += len(todo)
            vecs = self.encoder.encode(todo)
            new = {EmbeddingCache.key(t): v for t, v in zip(todo, vecs)}
            self.cache.put_many(new)
            found.update(new)
        return np.stack([found[k] for k in keys]) if keys else np.zeros((0, 1), dtype=np.float32)
