"""Compatibility exports for the local candidate review queue service."""

from dlsite_organizer.services.candidate_review_queue import (
    CandidateQueueService,
    CandidateReviewQueueService,
)

__all__ = ["CandidateQueueService", "CandidateReviewQueueService"]
