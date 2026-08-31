"""Download and prepare the static LLMLingua-2 OpenVINO model for the NPU."""

from pathlib import Path

import openvino as ov
from optimum.intel.openvino import OVModelForTokenClassification
from transformers import AutoTokenizer


MODEL_ID = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "models" / "llmlingua2-ov"
TARGET = ROOT / "models" / "llmlingua2-ov-npu-fp16" / "model.xml"
SEQUENCE_LENGTH = 128


def main():
    if not (SOURCE / "openvino_model.xml").exists():
        SOURCE.mkdir(parents=True, exist_ok=True)
        model = OVModelForTokenClassification.from_pretrained(MODEL_ID, export=True)
        model.save_pretrained(SOURCE)
        AutoTokenizer.from_pretrained(MODEL_ID).save_pretrained(SOURCE)

    if not TARGET.exists():
        core = ov.Core()
        model = core.read_model(SOURCE / "openvino_model.xml")
        model.reshape({input_.get_any_name(): [1, SEQUENCE_LENGTH] for input_ in model.inputs})
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        ov.save_model(model, TARGET, compress_to_fp16=True)
    print(TARGET)


if __name__ == "__main__":
    main()
