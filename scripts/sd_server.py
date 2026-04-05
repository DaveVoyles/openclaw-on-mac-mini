#!/usr/bin/env python3
"""Lightweight Stable Diffusion HTTP server for Apple Silicon Macs.

Runs locally on the Mac Mini host (not in Docker) to leverage the M4 Pro's
Metal Performance Shaders (MPS) GPU backend.

Usage:
    pip install diffusers transformers accelerate safetensors flask pillow torch
    python scripts/sd_server.py

The first run downloads the model (~5 GB). Subsequent starts load from cache.

Endpoints:
    GET  /health             → {"status": "ready", "model": "..."}
    POST /generate           → PNG image bytes
         Body: {"prompt": "...", "negative_prompt": "...",
                "width": 1024, "height": 1024, "steps": 20, "seed": -1}
"""

import io
import logging
import os
import time

from flask import Flask, jsonify, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sd_server")

app = Flask(__name__)

# Model selection — SDXL Turbo is fast and good quality on Apple Silicon
MODEL_ID = os.getenv("SD_MODEL", "stabilityai/sdxl-turbo")
PORT = int(os.getenv("SD_PORT", "7861"))

# Global pipeline (loaded once at startup)
_pipe = None


def _load_pipeline():
    """Load the Stable Diffusion pipeline with MPS backend."""
    global _pipe
    import torch
    from diffusers import AutoPipelineForText2Image

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    log.info("Loading %s on %s...", MODEL_ID, device)

    _pipe = AutoPipelineForText2Image.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16 if device == "mps" else torch.float32,
        variant="fp16" if device == "mps" else None,
    )
    _pipe = _pipe.to(device)

    # Enable memory-efficient attention if available
    try:
        _pipe.enable_attention_slicing()
    except Exception:
        pass

    log.info("Pipeline ready on %s", device)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ready" if _pipe is not None else "loading",
        "model": MODEL_ID,
    })


@app.route("/generate", methods=["POST"])
def generate():
    if _pipe is None:
        return jsonify({"error": "Model not loaded yet"}), 503

    data = request.get_json(force=True)
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    import torch

    negative_prompt = data.get("negative_prompt", "blurry, low quality, distorted, watermark")
    width = max(256, min(int(data.get("width", 1024)), 1536))
    height = max(256, min(int(data.get("height", 1024)), 1536))
    # Round to nearest 64
    width = (width // 64) * 64
    height = (height // 64) * 64
    steps = max(1, min(int(data.get("steps", 20)), 50))
    seed = int(data.get("seed", -1))

    generator = None
    if seed >= 0:
        generator = torch.Generator(device=_pipe.device.type).manual_seed(seed)

    log.info("Generating: %r (%dx%d, %d steps, seed=%d)", prompt[:80], width, height, steps, seed)
    start = time.monotonic()

    try:
        result = _pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            generator=generator,
        )
        image = result.images[0]

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        elapsed = time.monotonic() - start
        log.info("Generated in %.1fs", elapsed)

        return send_file(buf, mimetype="image/png")

    except Exception as e:
        log.error("Generation failed: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    log.info("Starting Stable Diffusion server on port %d...", PORT)
    _load_pipeline()
    app.run(host="0.0.0.0", port=PORT, threaded=False)
