"""Adapters: hand a router Instruction to the user's own AI tool."""

from .base import Adapter, AdapterResult
from .registry import available_adapters, get_adapter

__all__ = ["Adapter", "AdapterResult", "available_adapters", "get_adapter"]
