"""
UCL Research Module
==================

This module contains enhanced operational learning summary processors with improved
maintainability, error handling, caching, and performance monitoring.

Components:
- ops_learning_summary4.py: Consolidated summary processor with best practices
- Enhanced caching with Redis
- Performance monitoring and metrics
- Celery task support for async processing
- Comprehensive error handling and logging

Usage:
    from per.ucl_research.ops_learning_summary4 import (
        OpsLearningSummaryTask,
        DrefSummaryTask,
        PerformanceMonitor
    )
"""

__version__ = "1.0.0"
__author__ = "UCL Research Team Internships 2025"