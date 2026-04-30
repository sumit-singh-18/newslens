from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Generator, Optional

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def _resolve_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    database_path = os.getenv("DATABASE_PATH", "./newslens.db")
    return f"sqlite:///{database_path}"


DATABASE_URL = _resolve_database_url()
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, default=lambda: datetime.now(timezone.utc).date())

    scores: Mapped[list["ArticleScore"]] = relationship(back_populates="article", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_articles_topic", "topic"),
        Index("ix_articles_fetched_at", "fetched_at"),
        Index("ix_articles_snapshot_date", "snapshot_date"),
    )


class ArticleScore(Base):
    __tablename__ = "article_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    sentiment_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sentiment_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    bias_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bias_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    raw_scores: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    article: Mapped[Article] = relationship(back_populates="scores")


class TopicAnalysis(Base):
    __tablename__ = "topic_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_angle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_topic_analysis_topic", "topic"),
        Index("ix_topic_analysis_snapshot_date", "snapshot_date"),
    )


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
