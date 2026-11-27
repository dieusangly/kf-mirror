"""Access the original pilot dataset validator from a source checkout."""
from importlib.util import module_from_spec, spec_from_file_location

from knowledge_foundry.shared.hashing import file_hash
from knowledge_foundry.shared.paths import REPOSITORY_ROOT


def validate(manifest_path):
    path = REPOSITORY_ROOT / 'scripts/validate_aml_cft_reasoning_dataset.py'
    if file_hash(path) != '55ff804a1e3c26c9b9f8635ff682add119500e38af634a33ba9ccd344bbf734d':
        raise ValueError('pilot dataset validator hash differs')
    spec = spec_from_file_location('pilot_readable_dataset_v1', path)
    validator = module_from_spec(spec)
    spec.loader.exec_module(validator)
    return validator.validate(manifest_path)
