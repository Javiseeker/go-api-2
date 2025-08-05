"""
UCL Research Module
==================

This module contains enhanced operational learning summary processors with improved
maintainability, error handling, caching, and performance monitoring.

Components:
- ops_learning_summary4.py: Consolidated task classes with BaseAITask foundation
- ucl_views.py: Clean API views with minimal logic, delegating to task classes
- ifrc_client.py: Unified async HTTP client for IFRC API interactions
- rapid_response_parser.py: RR capacity questions processing with Excel generation
- Unified caching mechanisms via BaseAITask
- Performance monitoring and metrics
- Comprehensive error handling and logging

Task Classes:
    from per.ucl_research.ops_learning_summary4 import (
        BaseAITask,               # Base class with Azure OpenAI and caching
        OpsLearningSummaryTask,   # Complex ops learning analysis
        DrefSummaryTask,          # DREF-specific operations
        RRCapacityTask,           # Rapid response capacity processing
        PreviousCrisesTask,       # Previous crises insights
        PerformanceMonitor        # Performance tracking utilities
    )
"""

__version__ = "1.0.0"
__author__ = "UCL Research Team Internships 2025"