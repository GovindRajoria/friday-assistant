# skills/dev/gpu_status.py
"""What accelerators exist, what they are doing, and what OpenVINO can target.

Directly relevant to the work this assistant belongs to: the vision path here runs
YOLO11 through OpenVINO, and "why is inference slow" or "is anything else using the
GPU" is a question with a factual answer that nobody should be guessing at.

Two independent sources, because they answer different questions. `nvidia-smi`
reports NVIDIA hardware and its live utilisation and memory. OpenVINO's device
query reports what *this* runtime can actually target — CPU, GPU, NPU — which is
the list that determines where a model can be told to run. A machine with an
NVIDIA card that OpenVINO cannot see is a real and confusing state, and it is
worth being able to show both halves at once.

Terminal: the report is the whole answer.
"""
import shutil
import subprocess

TIMEOUT_SECONDS = 15
# The fields worth having, in nvidia-smi's own names.
QUERY_FIELDS = ["name", "memory.total", "memory.used", "utilization.gpu",
                "temperature.gpu", "driver_version"]


class GpuStatusSkill:
    def __init__(self):
        self.manifest = {
            "name": "gpu_status",
            "description": (
                "Reports the graphics and accelerator hardware on this machine: GPU model, "
                "memory used and total, utilisation, temperature, driver version, and which "
                "devices the OpenVINO runtime can target for inference. Use this when asked "
                "about the GPU, VRAM, accelerators, why inference is slow, or where a model "
                "can run. Its answer is complete — the turn ends when it returns."
            ),
            "parameters": [],
            "terminal": True,
        }

    def execute(self, params=None):
        sections = [self._nvidia(), self._openvino()]
        return {
            "status": "success",
            "message": "\n".join(section for section in sections if section),
            "data": {"sources": 2},
        }

    def _nvidia(self):
        binary = shutil.which("nvidia-smi")
        if not binary:
            return "No nvidia-smi on PATH, so either there is no NVIDIA GPU or its driver is not installed."

        try:
            result = subprocess.run(
                [binary, f"--query-gpu={','.join(QUERY_FIELDS)}",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"nvidia-smi is present but did not answer: {error}"

        if result.returncode != 0:
            return f"nvidia-smi failed: {result.stderr.strip() or result.returncode}"

        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return "nvidia-smi ran but reported no GPUs."

        rendered = []
        for index, line in enumerate(lines):
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < len(QUERY_FIELDS):
                rendered.append(f"  GPU {index}: {line.strip()}")
                continue
            name, total, used, util, temp, driver = fields[:6]
            free = self._free(total, used)
            rendered.append(f"  GPU {index}: {name} — {used}/{total} MiB used{free}, "
                            f"{util}% utilisation, {temp}C, driver {driver}")
        return f"{len(lines)} NVIDIA GPU(s):\n" + "\n".join(rendered)

    def _openvino(self):
        try:
            import openvino as ov
        except ImportError:
            return "OpenVINO is not installed, so I cannot say what it could target."

        try:
            core = ov.Core()
            devices = list(core.available_devices)
        except Exception as error:                                    # noqa: BLE001
            return f"OpenVINO is installed but could not enumerate devices: {error}"

        if not devices:
            return "OpenVINO reports no available devices, which would leave inference nowhere to run."

        described = []
        for device in devices:
            try:
                full_name = core.get_property(device, "FULL_DEVICE_NAME")
            except Exception:                                         # noqa: BLE001
                full_name = "name unavailable"
            described.append(f"  {device}: {full_name}")
        return (f"OpenVINO can target {len(devices)} device(s) — this is what a model can "
                f"actually be pointed at:\n" + "\n".join(described))

    @staticmethod
    def _free(total, used):
        try:
            return f" ({int(float(total)) - int(float(used))} MiB free)"
        except (TypeError, ValueError):
            return ""


def setup():
    return GpuStatusSkill()
