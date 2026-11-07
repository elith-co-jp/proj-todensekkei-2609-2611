"""OCR が読み違えやすい文字を、ローカルの画像対応 LLM に読み直させる（任意の機能）。

- 器具番号の形に合わない接点・コイルのラベル（830-1、0010 など）
- 日本語の装置名・信号名称・注記（OCR は簡体字に寄せて 盤→盐、記→记 と読む）

環境変数 LABEL_LLM_URL（Ollama 互換 API の起点。例: http://127.0.0.1:11434）と
LABEL_LLM_MODEL がどちらも設定されているときだけ使う。設定がなければ OCR の結果だけを使う。
"""

from __future__ import annotations

import base64
import io
import json
import os
import urllib.request
from typing import Any

from PIL import Image

from analysis.text_extraction import LabelReader

DEVICE_PROMPT = (
    "これは電気制御回路のシーケンス図の一部を拡大した画像です。中央の記号に付いている器具番号を読んでください。"
    "器具番号は 33HB、30R、43U、CN229、TB16 のような英数字です。端子番号の数字だけ（13、14 など）や "
    "C、NO、NC、COM は器具番号ではありません。器具番号が2行に分かれていれば空白で区切って両方書いてください。"
    "器具番号だけを答え、説明は書かないでください。読めなければ ? と答えてください。"
)
TEXT_PROMPT = (
    "これは電気制御回路のシーケンス図の一部を拡大した画像です。画像の中の文字列をそのまま書き写してください。"
    "英数字も含めて書き写し、説明は書かないでください。"
)
# (指示, 切り出しの余白 px)。器具番号は記号の枠ごと、文字は OCR の文字枠を少し広げて切り出す。
DEVICE = (DEVICE_PROMPT, 14)
TEXT = (TEXT_PROMPT, 4)
CROP_SCALE = 4


def crop_png(image: Image.Image, box: Any, padding: int) -> bytes:
    x0, y0, x1, y1 = (box["x0"], box["y0"], box["x1"], box["y1"]) if isinstance(box, dict) else box
    region = (int(x0) - padding, int(y0) - padding, int(x1) + padding, int(y1) + padding)
    piece = image.crop(region).convert("RGB")
    piece = piece.resize((piece.width * CROP_SCALE, piece.height * CROP_SCALE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    piece.save(buffer, format="PNG")
    return buffer.getvalue()


def ask(url: str, model: str, prompt: str, png: bytes, timeout: float) -> str:
    body = json.dumps({
        "model": model, "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 32},
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(png).decode()]}],
    }).encode()
    request = urllib.request.Request(f"{url.rstrip('/')}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return str(json.loads(response.read())["message"]["content"]).strip()


def make_reader(url: str, model: str, task: tuple[str, int] = DEVICE, timeout: float = 60.0) -> LabelReader:
    prompt, padding = task

    def read(image: Image.Image, box: Any) -> str | None:
        try:
            return ask(url, model, prompt, crop_png(image, box, padding), timeout) or None
        except (OSError, ValueError, KeyError):  # 読み直しに失敗したら OCR のラベルを残す
            return None

    return read


def reader_from_env(task: tuple[str, int] = DEVICE) -> LabelReader | None:
    url, model = os.environ.get("LABEL_LLM_URL"), os.environ.get("LABEL_LLM_MODEL")
    return make_reader(url, model, task) if url and model else None
