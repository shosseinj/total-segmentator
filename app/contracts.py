from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

@dataclass
class ObjectSnapshot:
    bucket: str
    key: str
    etag: str
    size: int
    version_id: str | None = None
    last_modified: str = ''
    sha256: str = ''

@dataclass
class DownloadedInput:
    path: Path
    sha256: str
    snapshot: ObjectSnapshot

@dataclass
class SeriesItem:
    study_uid: str
    series_uid: str
    modality: str
    frame_of_reference_uid: str
    paths: list[Path]
    eligibility: str = 'eligible'
    safe_error: str | None = None
    source_instances: list[str] = field(default_factory=list)
    geometry_summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class ActorContext:
    tenant_id: str
    actor_id: str
    operator: bool = True

@dataclass(frozen=True)
class JobLease:
    job_id: str
    token: str
    worker_id: str

class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ScanRequest(StrictRequest):
    source_id: str
    prefix: str = ''
    max_objects: int = Field(default=3, ge=1, le=20)
    object_keys: list[str] | None = Field(default=None, min_length=1, max_length=20)

class BatchRequest(StrictRequest):
    scan_id: str
    series_item_ids: list[str] = Field(min_length=1, max_length=100)
    task: Literal['total','total_mr'] = 'total'
    structures: list[str] = Field(min_length=1, max_length=20)
    quality: Literal['standard','fast'] = 'standard'
    outputs: list[str] = Field(default_factory=lambda:['dicom_seg_binary','nifti','measurements_json','manifest_json'])
