#!/usr/bin/env python3
"""Run on a rented Linux host. Resolves immutable identities before launching workers."""

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path

from llmschedbench.gpu import GPUConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="vLLM image tag or digest to resolve"
    )
    parser.add_argument(
        "--host-cuda-driver",
        action="store_true",
        help="prefer the host driver over container compatibility libraries",
    )
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--model", default="Qwen/Qwen3-4B")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--output", type=Path, default=Path("runs/gpu-host"))
    parser.add_argument("--max-model-len", type=int, default=16384)
    args = parser.parse_args()
    # Never silently replace existing deployment metadata or containers.
    args.output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["docker", "pull", args.image], check=True)
    inspect = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.image])
    )[0]
    digests = inspect.get("RepoDigests", [])
    if not digests:
        raise RuntimeError("image has no registry digest")
    url = f"https://huggingface.co/api/models/{args.model}/revision/{args.revision}"
    with urllib.request.urlopen(url, timeout=30) as response:
        model = json.load(response)
    config = GPUConfig(
        model=args.model,
        model_revision=model["sha"],
        image=digests[0],
        endpoints=[f"http://127.0.0.1:{8000 + i}" for i in range(args.workers)],
        max_model_len=args.max_model_len,
        host_cuda_driver=args.host_cuda_driver,
    )
    (args.output / "config.json").write_text(config.model_dump_json(indent=2) + "\n")
    (args.output / "image.json").write_text(json.dumps(inspect, indent=2) + "\n")
    gpu_info = subprocess.check_output(["nvidia-smi", "-q"], text=True)
    (args.output / "nvidia-smi.txt").write_text(gpu_info)
    launched = []
    try:
        for index in range(args.workers):
            name = f"llmschedbench-worker-{index}"
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--gpus",
                    f"device={index}",
                    "--ipc=host",
                    "-p",
                    f"127.0.0.1:{8000 + index}:8000",
                    "-v",
                    "llmschedbench-hf:/root/.cache/huggingface",
                    "-e",
                    "VLLM_SERVER_DEV_MODE=1",
                    *(
                        [
                            "-e",
                            "LD_LIBRARY_PATH=/lib/x86_64-linux-gnu:/usr/local/nvidia/lib64:/usr/local/cuda/lib64",
                        ]
                        if args.host_cuda_driver
                        else []
                    ),
                    config.image,
                    "--model",
                    config.model,
                    "--revision",
                    config.model_revision,
                    "--tokenizer-revision",
                    config.model_revision,
                    "--served-model-name",
                    config.model,
                    "--dtype",
                    "half",
                    "--max-model-len",
                    str(config.max_model_len),
                    "--block-size",
                    str(config.cache_block_size),
                    "--enable-prefix-caching",
                    "--generation-config",
                    "vllm",
                ],
                check=True,
            )
            launched.append(name)
    except BaseException:
        for name in launched:
            subprocess.run(["docker", "rm", "-f", name], check=False)
        raise
    print(f"Workers starting. Config: {args.output / 'config.json'}")
    print(
        "Wait for all /health endpoints before replay; retain this directory with results."
    )


if __name__ == "__main__":
    main()
