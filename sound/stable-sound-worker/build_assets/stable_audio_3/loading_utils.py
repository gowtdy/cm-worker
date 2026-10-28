import json
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import load_file
from stable_audio_3.factory import (
    create_autoencoder_from_config,
    create_diffusion_cond_from_config,
)

DEFAULT_T5GEMMA_SUBFOLDER = "t5gemma-b-b-ul2"


def apply_local_t5gemma_path(
    model_config: dict,
    *,
    config_path: str | None = None,
    t5gemma_path: str | None = None,
) -> dict:
    """Override t5gemma conditioner to load from a local directory in-memory.

    Priority:
      1. Explicit ``t5gemma_path`` (e.g. env T5GEMMA_PATH)
      2. ``dirname(config_path) / <subfolder or t5gemma-b-b-ul2>`` if that dir exists
      3. Keep official repo_id + subfolder (online / HF cache load)

    Does not write back to disk.
    """
    conditioning = model_config.get("conditioning")
    if conditioning is None:
        conditioning = model_config.get("model", {}).get("conditioning")
    if not conditioning:
        return model_config

    configs = conditioning.get("configs") or []
    model_dir = Path(config_path).expanduser().resolve().parent if config_path else None

    for conditioner_info in configs:
        if conditioner_info.get("type") != "t5gemma":
            continue

        cfg = conditioner_info.setdefault("config", {})
        subfolder = cfg.get("subfolder") or DEFAULT_T5GEMMA_SUBFOLDER

        local_path = None
        if t5gemma_path:
            candidate = Path(t5gemma_path).expanduser().resolve()
            if candidate.is_dir():
                local_path = candidate
            else:
                print(
                    f"T5GEMMA_PATH is set but not a directory: {candidate}; "
                    "falling back to auto-detect / remote"
                )
        if local_path is None and model_dir is not None:
            candidate = model_dir / subfolder
            if candidate.is_dir():
                local_path = candidate

        if local_path is None:
            continue

        cfg["model_path"] = str(local_path)
        cfg.pop("subfolder", None)
        print(f"Using local T5Gemma from {local_path}")

    return model_config


def copy_state_dict(model, state_dict):
    model_state_dict = model.state_dict()
    state_dict = remap_state_dict_keys(state_dict, model_state_dict)
    for key in state_dict:
        if (
            key in model_state_dict
            and state_dict[key].shape == model_state_dict[key].shape
        ):
            model_state_dict[key] = state_dict[key]
        else:
            print(
                f"Key {key} not found in target state_dict or shape mismatch. Skipping."
            )

    model.load_state_dict(model_state_dict, strict=False)


def load_autoencoder(config_path: str, ckpt_path: str, device: str = "cpu"):
    """Load only the autoencoder from a combined DiT+autoencoder checkpoint.

    Only pretransform tensors are read from disk, directly onto the target device.
    Standalone AE-only checkpoints (e.g. stabilityai/SAME-L / SAME-S) have no prefix.
    """

    with open(config_path) as f:
        config = json.load(f)

    autoencoder = create_autoencoder_from_config(config["model"], config["sample_rate"])

    # Full DiT checkpoints nest the AE under pretransform.model.*;
    # standalone AE-only checkpoints have no prefix.
    nested_prefix = "pretransform.model."
    with safe_open(ckpt_path, framework="pt", device=device) as f:
        all_keys = list(f.keys())
    if any(k.startswith(nested_prefix) for k in all_keys):
        effective_prefix = nested_prefix
    else:
        effective_prefix = ""  # standalone AE — keys are already bare
    with safe_open(ckpt_path, framework="pt", device=device) as f:
        state_dict = {
            k[len(effective_prefix) :]: f.get_tensor(k)
            for k in all_keys
            if k.startswith(effective_prefix)
        }

    copy_state_dict(autoencoder, state_dict)
    return autoencoder.to(device)


def load_diffusion_cond(
    model_config,
    ckpt_path: str,
    device: str = "cuda",
    model_half: bool = False,
):
    print(f'load_diffusion_cond, Loading diffusion cond from model_config:{model_config} and ckpt_path:{ckpt_path}')
    model = create_diffusion_cond_from_config(model_config)
    print(f'load_diffusion_cond, Creating diffusion cond from model_config:{model_config}')
    copy_state_dict(model, load_file(ckpt_path))
    print(f'load_diffusion_cond, Done loading diffusion cond')
    model.to(device).eval().requires_grad_(False)
    print(f'load_diffusion_cond, Done loading diffusion cond to device:{device}')
    if model_half:
        model.to(torch.float16)
        print(f'load_diffusion_cond, Done converting diffusion cond to float16')
    return model


def remap_state_dict_keys(state_dict, model_state_dict):
    remapped = {}
    for key, value in state_dict.items():
        if key not in model_state_dict:
            parts = key.split(".")
            for i in range(1, len(parts)):
                candidate = ".".join(parts[:i]) + "." + ".".join(parts[i + 1 :])
                if candidate in model_state_dict:
                    key = candidate
                    break
        remapped[key] = value
    return remapped
