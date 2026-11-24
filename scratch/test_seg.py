import json, time, sys, traceback
from pathlib import Path
from src.segmentation.pipeline import create_parser, run
from src.common.corpus import parse_args

print("Starting segmentation...")
t0 = time.time()
try:
    parser = create_parser()
    args = parse_args(parser, ["--config", "configs/CPO_19.json", "--force"])
    print(f"Args parsed: work_id={args.work_id}, data_root={args.data_root}")
    run(args)
    print(f"Done in {time.time() - t0:.2f}s")
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
