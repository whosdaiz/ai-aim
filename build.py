"""
Build TensorRT engines from every ONNX model in models/.

Compatible with TensorRT 10.x and 11.x:
  - TRT 10 and earlier: optional FP16 via BuilderFlag.FP16
  - TRT 11+: precision comes from the ONNX graph. These ONNX files are already
    FP16 internally with FP32 I/O so TensorRT casts on the GPU (faster than a
    Python float16 preprocess).

Default: walk models/*.onnx and write a matching .engine next to each one.
Pass --onnx to build a single file. Existing engines are skipped unless --force.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tensorrt as trt

PROJECT_DIR = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_DIR / "models"
TARGET_SHAPE = (1, 3, 640, 640)
WORKSPACE_BYTES = 2 << 30


def _trt_major_version() -> int:
    return int(trt.__version__.split(".")[0])


def _create_network(builder: trt.Builder) -> trt.INetworkDefinition:
    """Create a network definition compatible with the installed TensorRT version."""
    if _trt_major_version() < 11 and hasattr(trt.NetworkDefinitionCreationFlag, "EXPLICIT_BATCH"):
        flag = 1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
        return builder.create_network(flag)
    return builder.create_network()


def _configure_precision(config: trt.IBuilderConfig) -> str:
    """
    Enable FP16 when supported by this TensorRT version.

    TensorRT 11 removed BuilderFlag.FP16. Precision is defined by the ONNX graph
    (strong typing) rather than builder flags.
    """
    if hasattr(trt.BuilderFlag, "FP16"):
        config.set_flag(trt.BuilderFlag.FP16)
        return "fp16 (BuilderFlag.FP16)"
    return "fp16 weights / fp32 I/O (TensorRT 11 strongly typed)"


def iter_onnx_models(models_dir: Path = MODELS_DIR) -> list[Path]:
    if not models_dir.is_dir():
        return []
    return sorted(models_dir.glob("*.onnx"), key=lambda path: path.stem.lower())


def build_engine(
    onnx_path: Path,
    engine_path: Path,
    target_shape: tuple[int, int, int, int] = TARGET_SHAPE,
) -> None:
    if not onnx_path.is_file():
        raise FileNotFoundError(f"ONNX model not found: {onnx_path.resolve()}")

    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = _create_network(builder)
    parser = trt.OnnxParser(network, logger)

    print(f"TensorRT version: {trt.__version__}")
    print(f"Parsing ONNX: {onnx_path}")

    if not parser.parse(onnx_path.read_bytes()):
        for i in range(parser.num_errors):
            print(f"ONNX Error: {parser.get_error(i)}")
        raise RuntimeError("Failed to parse ONNX file.")

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, WORKSPACE_BYTES)
    if hasattr(config, "builder_optimization_level"):
        # Level 5 = max tactic search. Only affects one-time build time (this
        # model is tiny, build still takes ~1-2 min), never runtime latency.
        config.builder_optimization_level = 5
        print("Builder optimization level: 5 (max)")
    precision = _configure_precision(config)
    print(f"Build precision mode: {precision}")

    profile = builder.create_optimization_profile()
    for i in range(network.num_inputs):
        input_tensor = network.get_input(i)
        name = input_tensor.name
        shape = list(target_shape)
        profile.set_shape(name, shape, shape, shape)
        print(f"Configured profile for '{name}': {shape}")

    config.add_optimization_profile(profile)

    print("Building TensorRT engine... (optimization level 5 searches more tactics; expect ~5-10 minutes)")
    serialized_engine = builder.build_serialized_network(network, config)
    if serialized_engine is None:
        raise RuntimeError("Engine build failed.")

    engine_path.parent.mkdir(parents=True, exist_ok=True)
    engine_path.write_bytes(serialized_engine)
    print(f"Successfully created: {engine_path.resolve()}")


def build_all(
    models_dir: Path = MODELS_DIR,
    target_shape: tuple[int, int, int, int] = TARGET_SHAPE,
    force: bool = False,
) -> None:
    models = iter_onnx_models(models_dir)
    if not models:
        raise FileNotFoundError(f"No .onnx files in {models_dir.resolve()}")

    print(f"Found {len(models)} ONNX model(s) in {models_dir}")
    failed: list[tuple[str, str]] = []
    built = 0
    skipped = 0
    for index, onnx_path in enumerate(models, 1):
        engine_path = models_dir / f"{onnx_path.stem}.engine"
        print(f"\n[{index}/{len(models)}] {onnx_path.name} -> {engine_path.name}")
        if engine_path.is_file() and not force:
            print(f"Skipping {engine_path.name} (already exists; use --force to rebuild)")
            skipped += 1
            continue
        try:
            build_engine(onnx_path, engine_path, target_shape)
            built += 1
        except Exception as exc:
            print(f"FAILED {onnx_path.name}: {exc}", file=sys.stderr)
            failed.append((onnx_path.name, str(exc)))

    print(f"\nDone. built={built} skipped={skipped} failed={len(failed)}")
    if failed:
        for name, err in failed:
            print(f"  {name}: {err}", file=sys.stderr)
        raise RuntimeError(f"{len(failed)} engine(s) failed")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onnx",
        type=Path,
        default=None,
        help="Build only this ONNX file. Default is every .onnx in models/.",
    )
    parser.add_argument(
        "--engine",
        type=Path,
        default=None,
        help="Output .engine path when building a single --onnx file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild engines even if the .engine file already exists.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=TARGET_SHAPE[-1],
        help=(
            "Square model input resolution, e.g. 640, 512, 480, 416. The ROI "
            "captured on screen stays 640x640 regardless (vision_tracker.py "
            "rescales detections back to ROI pixel space automatically), so "
            "a smaller value here trades some small-target precision for "
            "meaningfully less GPU work per frame -- useful when the GPU is "
            "shared with the game and contention is capping detection FPS."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    shape = (1, 3, args.size, args.size)
    try:
        if args.onnx is not None:
            onnx_path = args.onnx
            engine_path = args.engine if args.engine is not None else onnx_path.with_suffix(".engine")
            if engine_path.is_file() and not args.force:
                print(f"Skipping {engine_path.name} (already exists; use --force to rebuild)")
            else:
                build_engine(onnx_path=onnx_path, engine_path=engine_path, target_shape=shape)
        else:
            build_all(target_shape=shape, force=args.force)
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        raise
