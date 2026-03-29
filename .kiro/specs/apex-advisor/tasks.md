# Implementation Plan: Apex Advisor

## Overview

This implementation plan converts the Apex Advisor feature design into a series of coding tasks for a Python-based backend system. The system follows a four-layer Profile architecture (Team/Company/Work/Industry) with specialized AI modules for intelligent processing. The implementation will use FastAPI for the backend, SQLAlchemy for data persistence, and integrate with LLM APIs for AI functionality.

## Tasks

- [x] 1. Set up project structure and core infrastructure
  - Create Python project structure with FastAPI
  - Set up SQLAlchemy models and database configuration
  - Configure environment variables and settings
  - Set up basic authentication with JWT
  - Create base API structure and error handling
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Implement user authentication and profile initialization
  - [x] 2.1 Create User model and authentication endpoints
    - Implement User SQLAlchemy model with password hashing
    - Create registration endpoint that initializes empty four-layer Profile
    - Implement login/logout endpoints with JWT token management
    - Add token refresh mechanism
    - _Requirements: 1.1, 1.2, 1.3_

  - [x]* 2.2 Write property test for user authentication
    - **Property 3: Invalid credentials rejection**
    - **Validates: Requirements 1.3**

  - [ ]* 2.3 Write property test for profile initialization
    - **Property 2: Registration creates empty Profile structure**
    - **Validates: Requirements 1.2**

- [x] 3. Implement Team Profile management (Layer 1)
  - [x] 3.1 Create TeamMember model and CRUD operations
    - Implement TeamMember SQLAlchemy model with all required fields
    - Create API endpoints for team member management
    - Add validation for required fields (name, role)
    - _Requirements: 2.1, 2.2, 2.4, 2.5_

  - [x] 3.2 Implement BehaviorTag model and AI profiling system
    - Create BehaviorTag model with confidence scoring
    - Implement Behavior_Profiler AI module with LLM integration
    - Create multi-turn conversation endpoints for AI profiling
    - Add tag generation and confirmation workflow
    - _Requirements: 2.6, 2.7, 2.8, 2.9, 2.10_

  - [ ]* 3.3 Write property test for behavior tag generation
    - **Property 6: Behavior tag format validation**
    - **Validates: Requirements 2.7**

  - [ ]* 3.4 Write property test for team member data persistence
    - **Property 4: Profile data CRUD round trip**
    - **Validates: Requirements 2.1, 2.3, 2.9**

  - [x] 3.5 Implement team member archive page with structured display
    - Create team member detail view with confidence visualization
    - Implement Taboo warning display with visual indicators
    - Add AI profiling conversation interface
    - _Requirements: 2.14, 2.15, 2.16_

- [x] 4. Checkpoint - Ensure team management tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Company Profile management (Layer 2)
  - [x] 5.1 Create Company Intelligence models and data structures
    - Implement CompanyIntelligence SQLAlchemy models
    - Create IntelligenceInput and IntelligenceDigest models
    - Add StakeholderNote model for key personnel tracking
    - _Requirements: 3.1, 3.8_

  - [x] 5.2 Implement Intelligence_Classifier AI module
    - Create Intelligence_Classifier with four-dimension analysis
    - Implement content classification and extraction logic
    - Add minimum input length validation (50 characters)
    - Create incremental update and conflict detection
    - _Requirements: 3.2, 3.3, 3.4, 3.6, 3.9_

  - [ ]* 5.3 Write property test for intelligence classification
    - **Property 8: Four-dimension structure validation**
    - **Validates: Requirements 3.2**

  - [ ]* 5.4 Write property test for incremental intelligence merging
    - **Property 9: Intelligence incremental merging**
    - **Validates: Requirements 3.4, 3.6**

  - [x] 5.5 Create company intelligence knowledge base interface
    - Implement four-dimension tabbed interface
    - Add real-time insights banner with 24h highlighting
    - Create stakeholder management interface
    - _Requirements: 3.5, 3.7, 3.10_

- [x] 6. Implement Work Profile management (Layer 3)
  - [x] 6.1 Create Work Profile models and deliverable standards
    - Implement DeliverableStandard model with five dimensions
    - Create Project, MeetingNote, and Workstream models
    - Add FollowUpItem model with optional associations
    - _Requirements: 4.1, 4.2, 4.4, 4.15, 4.17, 4.18_

  - [x] 6.2 Implement Deliverable_Review AI module
    - Create review engine with five-dimension evaluation
    - Implement ReviewHistory tracking and persistence
    - Add DeliverablePattern analysis for trend detection
    - Create review report generation with compliance scoring
    - _Requirements: 4.5, 4.6, 4.7, 4.10, 4.11, 4.12, 4.13, 4.14_

  - [ ]* 6.3 Write property test for deliverable review structure
    - **Property 12: Five-dimension review report structure**
    - **Validates: Requirements 4.5, 4.6**

  - [ ]* 6.4 Write property test for review history accumulation
    - **Property 19: Review history and pattern analysis**
    - **Validates: Requirements 4.12, 4.13, 4.14**

  - [x] 6.5 Implement FollowUp_Extractor AI module
    - Create meeting note analysis and action item extraction
    - Implement automatic workstream association logic
    - Add follow-up item confirmation and persistence workflow
    - _Requirements: 4.19, 4.20, 4.21, 4.22_

  - [ ]* 6.6 Write property test for follow-up extraction completeness
    - **Property 20: Follow-up extraction and persistence**
    - **Validates: Requirements 4.19, 4.20, 4.21**

- [x] 7. Implement Industry Profile management (Layer 4)
  - [x] 7.1 Create Industry Intelligence models and topic materials
    - Implement IndustryIntelligence SQLAlchemy models
    - Create IndustryInput and IndustryDigest models
    - Add TopicMaterial model for conversation topics
    - _Requirements: 5.1, 5.7, 5.8_

  - [x] 7.2 Implement Industry_Classifier AI module
    - Create Industry_Classifier with four-dimension analysis
    - Implement conflict detection and flagging system
    - Add topic material generation alongside intelligence extraction
    - Create update reminder system for data freshness
    - _Requirements: 5.2, 5.3, 5.4, 5.6, 5.13_

  - [ ]* 7.3 Write property test for industry conflict detection
    - **Property 10: Industry conflict detection and annotation**
    - **Validates: Requirements 5.6**

  - [ ]* 7.4 Write property test for topic material generation
    - **Property 11: Topic material generation completeness**
    - **Validates: Requirements 5.7**

  - [x] 7.5 Create industry intelligence interface with data visualization
    - Implement four-dimension tabbed interface
    - Add structured data card display for data highlights
    - Create topic material library with tag filtering
    - Add real-time insights area with conflict highlighting
    - _Requirements: 5.5, 5.9, 5.12, 5.14, 5.15_

- [x] 8. Checkpoint - Ensure all profile layers are functional
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement Prompt Engine and template generation
  - [x] 9.1 Create Prompt_Engine AI module
    - Implement scene-based prompt template generation
    - Create five predefined scene types with injection logic
    - Add placeholder mechanism for missing profile data
    - Implement Taboo constraint injection for team members
    - _Requirements: 6.1, 6.2, 6.4, 6.6, 7.1, 7.2_

  - [x]* 9.2 Write property test for prompt profile injection
    - **Property 13: Prompt generation includes Profile content**
    - **Validates: Requirements 3.10, 4.9, 5.11, 6.1**

  - [x]* 9.3 Write property test for Taboo constraint injection
    - **Property 16: Taboo information injection in prompts**
    - **Validates: Requirements 7.1, 7.2**

  - [x] 9.4 Implement custom template management
    - Create template saving and retrieval system
    - Add template refresh marking when profiles update
    - Implement template versioning and user customization
    - _Requirements: 6.5, 6.7_

  - [x]* 9.5 Write property test for template refresh marking
    - **Property 15: Profile updates trigger template refresh**
    - **Validates: Requirements 6.7**

- [x] 10. Implement unified Chatbox system
  - [x] 10.1 Create global AI conversation interface
    - Implement context-aware chatbox with profile integration
    - Add Context_Tag selection for manual profile layer specification
    - Create dual-box layout with main response and chain-of-thought
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.8_

  - [x] 10.2 Implement slash command system
    - Create slash command parser and execution engine
    - Implement predefined commands: behavior prediction, draft review, prompt generation, industry insights
    - Add command auto-completion and help system
    - _Requirements: 9.5, 9.6_

  - [x] 10.3 Add session management and source attribution
    - Implement per-session conversation context
    - Add source attribution for profile data usage
    - Create empty profile guidance and onboarding
    - _Requirements: 9.7, 9.9_

- [x] 11. Implement contextual nudge system
  - [x] 11.1 Create contextual awareness and notification system
    - Implement time-based update reminders for profiles
    - Add pattern-based suggestions for deliverable reviews
    - Create non-intrusive banner and card notification system
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_

- [x] 12. Implement data synchronization and persistence
  - [x] 12.1 Create cloud synchronization system
    - Implement real-time cloud data persistence (1-second target)
    - Add offline mode with local SQLite caching
    - Create conflict detection and resolution system
    - Implement cross-device synchronization (5-second target)
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ]* 12.2 Write property test for offline synchronization
    - **Property 17: Offline editing and sync behavior**
    - **Validates: Requirements 8.3**

  - [ ]* 12.3 Write property test for conflict detection
    - **Property 18: Conflict detection and user choice**
    - **Validates: Requirements 8.4**

- [x] 13. Integration and API wiring
  - [x] 13.1 Wire all AI modules with ProfileService
    - Connect all AI modules to profile management endpoints
    - Implement proper error handling and timeout management
    - Add API rate limiting and request validation
    - Create comprehensive API documentation
    - _Requirements: All layers integration_

  - [x] 13.2 Implement comprehensive error handling
    - Add LLM API timeout and retry logic (3 attempts with exponential backoff)
    - Implement input validation and format error handling
    - Create user-friendly error messages and recovery suggestions
    - Add logging and monitoring for AI module failures
    - _Requirements: Error handling specifications_

  - [x]* 13.3 Write integration tests for end-to-end workflows
    - Test complete user journey from registration to prompt generation
    - Validate AI module integration and data flow
    - Test offline/online synchronization scenarios

- [x] 14. Final checkpoint and performance validation
  - Ensure all tests pass, ask the user if questions arise.
  - Validate performance targets: login 500ms, save 1s, prompt generation 3s, review 5s (`tests/test_performance_checkpoint.py`; CI budgets scaled via `PERF_CI_MULTIPLIER`, default 4×)
  - Verify correctness properties: `tests/test_properties_inventory.py` maps the 16 numbered properties in this spec to tests and/or optional tasks (17–18 covered by optional 12.2–12.3)

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- All AI modules integrate with LLM APIs (OpenAI/Claude) for intelligent processing
- Property tests validate universal correctness properties with 100+ iterations each
- The system uses FastAPI + SQLAlchemy + PostgreSQL for robust backend architecture
- Checkpoints ensure incremental validation and user feedback opportunities
项目	说明
协议	与当前 OpenAIService 兼容，主要差 base URL + 模型名
你现在能做的	在 Kimi 平台拿 API Key 和 模型 id，确认文档里的 base URL
仓库里还缺的	可配置的 API 根地址，否则无法真正「走 Kimi」
如果你愿意，在 Agent 模式下可以说一声「加上 OPENAI_BASE_URL 并默认 OpenAI」，我可以按你项目结构改 config.py 和 OpenAIService，这样你只改 .env 就能在 OpenAI 和 Kimi 之间切换。



