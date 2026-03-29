from typing import Any, Dict, Union
from uuid import UUID

from sqlalchemy.orm import Session
from app.models.user import User
from app.models.company import CompanyProfile
from app.models.work import WorkProfile
from app.models.industry import IndustryProfile


class ProfileService:
    """Service for managing user profiles across all four layers."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def initialize_empty_profiles(self, user_id: Union[UUID, str]) -> Dict[str, Any]:
        """Initialize empty four-layer Profile structure for new user.
        
        This creates the basic structure for:
        - Layer 1: Team Profile (Know Your Team) - handled by TeamMember model
        - Layer 2: Company Profile (Know Your Company) 
        - Layer 3: Work Profile (Know Your Work)
        - Layer 4: Industry Profile (Know Your Industry)
        """
        uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        # Create empty Company Profile
        company_profile = CompanyProfile(user_id=uid)
        self.db.add(company_profile)
        
        # Create empty Work Profile
        work_profile = WorkProfile(user_id=uid)
        self.db.add(work_profile)
        
        # Create empty Industry Profile
        industry_profile = IndustryProfile(user_id=uid)
        self.db.add(industry_profile)
        
        # Commit all profiles
        self.db.commit()
        
        return {
            "team_profile": {
                "members": [],
                "created_at": None,
                "updated_at": None
            },
            "company_profile": {
                "id": str(company_profile.id),
                "intelligence": {
                    "business_performance": [],
                    "strategic_direction": [],
                    "cultural_signals": [],
                    "internal_rules": []
                },
                "stakeholders": [],
                "created_at": company_profile.created_at.isoformat(),
                "updated_at": company_profile.updated_at.isoformat()
            },
            "work_profile": {
                "id": str(work_profile.id),
                "deliverable_standards": [],
                "projects": [],
                "meeting_notes": [],
                "workstreams": [],
                "follow_up_items": [],
                "created_at": work_profile.created_at.isoformat(),
                "updated_at": work_profile.updated_at.isoformat()
            },
            "industry_profile": {
                "id": str(industry_profile.id),
                "intelligence": {
                    "industry_trends": [],
                    "competitive_landscape": [],
                    "regulatory_dynamics": [],
                    "data_highlights": []
                },
                "topic_materials": [],
                "created_at": industry_profile.created_at.isoformat(),
                "updated_at": industry_profile.updated_at.isoformat()
            }
        }
    
    def get_user_profiles(self, user_id: Union[UUID, str]) -> Dict[str, Any]:
        """Get all profile data for a user."""
        uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        user = self.db.query(User).filter(User.id == uid).first()
        if not user:
            return {}
        
        # Get team members (Layer 1)
        team_members = [
            {
                "id": str(member.id),
                "name": member.name,
                "role": member.role,
                "personality_traits": member.personality_traits,
                "communication_preference": member.communication_preference,
                "decision_style": member.decision_style,
                "reporting_preference": member.reporting_preference,
                "taboo": member.taboo,
                "notes": member.notes,
                "behavior_tags": [
                    {
                        "id": str(tag.id),
                        "label": tag.label,
                        "category": tag.category,
                        "confidence": tag.confidence,
                        "source": tag.source
                    } for tag in member.behavior_tags
                ]
            } for member in user.team_members
        ]
        
        # Get company profile (Layer 2)
        company_profile = user.company_profile
        company_data = {
            "intelligence": {
                "business_performance": [],
                "strategic_direction": [],
                "cultural_signals": [],
                "internal_rules": []
            },
            "stakeholders": [],
            "created_at": None,
            "updated_at": None
        }
        
        if company_profile:
            company_data.update({
                "id": str(company_profile.id),
                "created_at": company_profile.created_at.isoformat(),
                "updated_at": company_profile.updated_at.isoformat()
            })
            
            # Group intelligence items by dimension
            for item in company_profile.intelligence_items:
                if item.dimension not in company_data["intelligence"]:
                    company_data["intelligence"][item.dimension] = []
                st = item.source_type
                st_val = st.value if hasattr(st, "value") else st
                company_data["intelligence"][item.dimension].append({
                    "id": str(item.id),
                    "content": item.content,
                    "source_type": st_val,
                    "intelligence_input_id": str(item.intelligence_input_id)
                    if item.intelligence_input_id
                    else None,
                    "highlight_until": item.highlight_until.isoformat()
                    if item.highlight_until
                    else None,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat()
                })
            
            # Add stakeholder notes
            company_data["stakeholders"] = [
                {
                    "id": str(note.id),
                    "name": note.name,
                    "department": note.department,
                    "influence_description": note.influence_description
                } for note in company_profile.stakeholder_notes
            ]
        
        # Get work profile (Layer 3)
        work_profile = user.work_profile
        work_data = {
            "deliverable_standards": [],
            "projects": [],
            "meeting_notes": [],
            "workstreams": [],
            "follow_up_items": [],
            "created_at": None,
            "updated_at": None
        }
        
        if work_profile:
            work_data.update({
                "id": str(work_profile.id),
                "created_at": work_profile.created_at.isoformat(),
                "updated_at": work_profile.updated_at.isoformat()
            })
            
            # Add all work profile components
            work_data["deliverable_standards"] = [
                {
                    "id": str(std.id),
                    "work_scene": std.work_scene,
                    "format_requirements": std.format_requirements,
                    "style_requirements": std.style_requirements,
                    "thinking_framework": std.thinking_framework,
                    "perspective_and_stance": std.perspective_and_stance,
                    "habit_preferences": std.habit_preferences
                } for std in work_profile.deliverable_standards
            ]
            
            work_data["projects"] = [
                {
                    "id": str(proj.id),
                    "name": proj.name,
                    "status": proj.status,
                    "deadline": proj.deadline.isoformat() if proj.deadline else None,
                    "description": proj.description
                } for proj in work_profile.projects
            ]
            
            work_data["meeting_notes"] = [
                {
                    "id": str(note.id),
                    "title": note.title,
                    "meeting_date": note.meeting_date.isoformat(),
                    "participants": note.participants,
                    "content": note.content
                } for note in work_profile.meeting_notes
            ]
            
            work_data["workstreams"] = [
                {
                    "id": str(ws.id),
                    "name": ws.name,
                    "progress_description": ws.progress_description,
                    "goals_and_requirements": ws.goals_and_requirements,
                    "owner": ws.owner,
                    "status": ws.status
                } for ws in work_profile.workstreams
            ]
            
            work_data["follow_up_items"] = [
                {
                    "id": str(item.id),
                    "description": item.description,
                    "owner": item.owner,
                    "due_date": item.due_date.isoformat() if item.due_date else None,
                    "status": item.status,
                    "meeting_note_id": str(item.meeting_note_id) if item.meeting_note_id else None,
                    "workstream_id": str(item.workstream_id) if item.workstream_id else None
                } for item in work_profile.follow_up_items
            ]
        
        # Get industry profile (Layer 4)
        industry_profile = user.industry_profile
        industry_data = {
            "intelligence": {
                "industry_trends": [],
                "competitive_landscape": [],
                "regulatory_dynamics": [],
                "data_highlights": []
            },
            "topic_materials": [],
            "created_at": None,
            "updated_at": None
        }
        
        if industry_profile:
            industry_data.update({
                "id": str(industry_profile.id),
                "created_at": industry_profile.created_at.isoformat(),
                "updated_at": industry_profile.updated_at.isoformat()
            })
            
            # Group industry items by dimension
            for item in industry_profile.industry_items:
                if item.dimension not in industry_data["intelligence"]:
                    industry_data["intelligence"][item.dimension] = []
                cf = item.conflict_flag
                cf_val = cf.value if cf is not None and hasattr(cf, "value") else cf
                st = item.source_type
                st_val = st.value if hasattr(st, "value") else st
                industry_data["intelligence"][item.dimension].append({
                    "id": str(item.id),
                    "content": item.content,
                    "source_type": st_val,
                    "conflict_flag": cf_val,
                    "industry_input_id": str(item.industry_input_id)
                    if item.industry_input_id
                    else None,
                    "data_card": item.data_card,
                    "highlight_until": item.highlight_until.isoformat()
                    if item.highlight_until
                    else None,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat()
                })
            
            # Add topic materials
            industry_data["topic_materials"] = [
                {
                    "id": str(material.id),
                    "title": material.title,
                    "core_insight": material.core_insight,
                    "data_support": material.data_support,
                    "tags": list(material.tags or []),
                    "created_at": material.created_at.isoformat(),
                    "updated_at": material.updated_at.isoformat()
                } for material in industry_profile.topic_materials
            ]
        
        return {
            "team_profile": {
                "members": team_members,
                "created_at": None,
                "updated_at": None
            },
            "company_profile": company_data,
            "work_profile": work_data,
            "industry_profile": industry_data
        }