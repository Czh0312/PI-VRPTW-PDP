"""Vehicle-routing models and solver infrastructure."""

from .catalog import MODEL_SPECS, ModelSpec, require_model_number

__all__ = ["MODEL_SPECS", "ModelSpec", "require_model_number"]
