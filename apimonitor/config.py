"""Configuration management"""

from dataclasses import dataclass

@dataclass
class Config:
    api_key: str
    endpoint: str = "https://api.yourmonitor.com/ingest"
    batch_size: int = 100
    batch_timeout: int = 10  # seconds
    sampling_rate: float = 1.0  # 0.0 to 1.0
    debug: bool = False