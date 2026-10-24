from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateNote(StrictRequest):
    model: str = Field(default="Basic", min_length=1)
    deck: str = Field(default="Default", min_length=1)
    fields: dict[str, str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags):
        if any(not tag or any(c.isspace() for c in tag) for tag in tags):
            raise ValueError("Tags must be nonempty and contain no whitespace")
        return list(dict.fromkeys(tags))


class UpdateNote(StrictRequest):
    fields: dict[str, str] = Field(default_factory=dict)
    tags: list[str] | None = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags):
        return None if tags is None else CreateNote.validate_tags(tags)


class SyncRequest(StrictRequest):
    mode: Literal["incremental", "download"] = "incremental"


class Note(BaseModel):
    id: int
    guid: str
    model: str
    modified_seconds: int
    fields: dict[str, str]
    tags: list[str]


class Deck(BaseModel):
    id: int
    name: str


class Card(BaseModel):
    id: int
    note_id: int
    deck_id: int
    deck: str
    ordinal: int
    modified_seconds: int
    type: int
    queue: int
    due: int
    interval: int
    repetitions: int
    lapses: int


class Review(BaseModel):
    id: int
    card_id: int
    ease: int
    interval: int
    last_interval: int
    factor: int
    duration_ms: int
    type: int


Item = TypeVar("Item")


class Page(BaseModel, Generic[Item]):
    items: list[Item]
    next_cursor: int | None


class SyncResult(BaseModel):
    status: str
    mode: Literal["incremental", "download"]
