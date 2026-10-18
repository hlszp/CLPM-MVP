"""Replay the same independent tests against in-memory guard removal only."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import unittest

D = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("original", "remove-identity", "remove-arguments"))
    args = parser.parse_args()
    source = D / "source-original.py"
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "c27d789a2c8308cfbeebc692d4565d2460b6c65fe2f3bc295fc72f51469fb3c8"
    spec = importlib.util.spec_from_file_location("independent_fixed_controls", D / "verify_adapter_independent.py")
    controls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controls)
    original_setup = controls.AdapterControls.setUp

    def setup(self):
        original_setup(self)
        if args.mode == "remove-identity":
            self.module.check_identity = lambda info: None
        elif args.mode == "remove-arguments":
            self.module.validate = lambda argv, target: None

    controls.AdapterControls.setUp = setup
    names = ["test_wrong_container_id_name_or_image_never_dump", "test_missing_or_wrong_port_mapping_never_dump", "test_wrong_args_and_scope_are_refused_before_any_subprocess"]
    if args.mode == "remove-identity":
        names = names[:2]
    elif args.mode == "remove-arguments":
        names = names[2:]
    suite = unittest.TestSuite(controls.AdapterControls(name) for name in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert source.read_bytes() == raw and controls.SOURCE.read_bytes() == raw
    print(f"mode={args.mode}; actualDocker=0; actualDatabase=0; actualDump=0; unchangedSourceSha256={hashlib.sha256(raw).hexdigest()}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
