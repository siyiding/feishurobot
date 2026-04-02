"""
M6: Git Repository Integration Service.

Provides:
- GitHub/GitLab API integration
- Merge Request (MR) event handling
- Webhook endpoint for MR events
- Polling mechanism for MR updates
- File change tracking

8.1 Git仓接入 (3 days)
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

import httpx
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    GitProvider, GitMRStatus, GitChangedFile, GitCommit, GitMergeRequest,
    GitRepository, ModuleMapping, ChangeCaseMatch
)

logger = get_logger(__name__)


class GitServiceError(Exception):
    """Git service error."""
    pass


class GitService:
    """
    Git repository integration service.
    
    Supports:
    - GitHub API (api.github.com)
    - GitLab API (gitlab.com or self-hosted)
    - Webhook signature verification
    - MR event parsing
    """
    
    # GitHub API endpoints
    GITHUB_API_BASE = "https://api.github.com"
    
    # GitLab API endpoints (configurable)
    GITLAB_API_BASE = "https://gitlab.com/api/v4"
    
    def __init__(self):
        settings = get_settings()
        self.settings = settings
        self._github_tokens: Dict[str, str] = {}  # repo_id -> token
        self._gitlab_tokens: Dict[str, str] = {}  # repo_id -> token
        self._repositories: Dict[str, GitRepository] = {}
        self._module_mappings: List[ModuleMapping] = []
    
    # ==================== Repository Management ====================
    
    def register_repository(
        self,
        repo_id: str,
        name: str,
        full_name: str,
        provider: GitProvider,
        api_token: str = "",
        webhook_secret: str = "",
        api_url: Optional[str] = None,
    ) -> GitRepository:
        """
        Register a Git repository for monitoring.
        
        Args:
            repo_id: Unique repository identifier
            name: Repository name
            full_name: Owner/repo format
            provider: github or gitlab
            api_token: API access token
            webhook_secret: Secret for webhook signature verification
            api_url: Custom API URL for self-hosted GitLab
        """
        repo = GitRepository(
            repo_id=repo_id,
            name=name,
            full_name=full_name,
            provider=provider,
            webhook_secret=webhook_secret,
            api_url=api_url,
        )
        self._repositories[repo_id] = repo
        
        if provider == GitProvider.GITHUB:
            self._github_tokens[repo_id] = api_token
        elif provider == GitProvider.GITLAB:
            self._gitlab_tokens[repo_id] = api_token
        
        logger.info(f"Registered repository: {repo_id} ({provider.value})")
        return repo
    
    def get_repository(self, repo_id: str) -> Optional[GitRepository]:
        """Get repository by ID."""
        return self._repositories.get(repo_id)
    
    def list_repositories(self) -> List[GitRepository]:
        """List all registered repositories."""
        return list(self._repositories.values())
    
    # ==================== GitHub API Integration ====================
    
    async def _github_request(
        self,
        repo_id: str,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make authenticated GitHub API request."""
        repo = self._repositories.get(repo_id)
        if not repo:
            raise GitServiceError(f"Repository {repo_id} not found")
        
        token = self._github_tokens.get(repo_id, "")
        if not token:
            raise GitServiceError(f"No GitHub token for repository {repo_id}")
        
        url = f"{self.GITHUB_API_BASE}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
            
            if response.status_code == 401:
                raise GitServiceError(f"GitHub API unauthorized for {repo_id}")
            if response.status_code == 404:
                raise GitServiceError(f"GitHub resource not found: {endpoint}")
            if response.status_code >= 400:
                raise GitServiceError(f"GitHub API error: {response.status_code} - {response.text}")
            
            return response.json()
    
    async def get_github_mr(self, repo_id: str, mr_number: int) -> Optional[GitMergeRequest]:
        """Get a single Merge Request from GitHub."""
        try:
            data = await self._github_request(repo_id, "GET", f"/repos/{self._repositories[repo_id].full_name}/pulls/{mr_number}")
            
            # Get commits
            commits_data = await self._github_request(repo_id, "GET", f"/repos/{self._repositories[repo_id].full_name}/pulls/{mr_number}/commits")
            
            # Get changed files
            files_data = await self._github_request(repo_id, "GET", f"/repos/{self._repositories[repo_id].full_name}/pulls/{mr_number}/files")
            
            return self._parse_github_mr(data, commits_data, files_data)
        except Exception as e:
            logger.error(f"Failed to get GitHub MR: {e}")
            return None
    
    def _parse_github_mr(
        self,
        mr_data: Dict,
        commits_data: List[Dict],
        files_data: List[Dict]
    ) -> GitMergeRequest:
        """Parse GitHub PR data into GitMergeRequest."""
        # Parse commits
        commits = []
        for commit in commits_data:
            changed_files = []
            # Note: GitHub commits API doesn't directly list files
            # We'd need separate API calls or rely on PR files
            changed_files = [
                GitChangedFile(
                    filename=f.get("filename", ""),
                    status=f.get("status", "modified"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                ) for f in files_data if f.get("sha", "").startswith(commit.get("sha", "")[:6])
            ]
            
            commits.append(GitCommit(
                sha=commit.get("sha", ""),
                message=commit.get("commit", {}).get("message", ""),
                author=commit.get("commit", {}).get("author", {}).get("name", ""),
                author_email=commit.get("commit", {}).get("author", {}).get("email", ""),
                committed_at=commit.get("commit", {}).get("author", {}).get("date", ""),
                changed_files=changed_files,
            ))
        
        # Parse changed files
        changed_files = [
            GitChangedFile(
                filename=f.get("filename", ""),
                status=f.get("status", "modified"),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
            ) for f in files_data
        ]
        
        # Determine status
        if mr_data.get("merged"):
            status = GitMRStatus.MERGED
        elif mr_data.get("state") == "closed":
            status = GitMRStatus.CLOSED
        else:
            status = GitMRStatus.OPEN
        
        return GitMergeRequest(
            mr_id=str(mr_data.get("number", "")),
            title=mr_data.get("title", ""),
            description=mr_data.get("body", ""),
            source_branch=mr_data.get("head", {}).get("ref", ""),
            target_branch=mr_data.get("base", {}).get("ref", ""),
            author=mr_data.get("user", {}).get("login", ""),
            status=status,
            web_url=mr_data.get("html_url", ""),
            created_at=mr_data.get("created_at", ""),
            updated_at=mr_data.get("updated_at", ""),
            commits=commits,
            changed_files=changed_files,
        )
    
    async def list_github_mrs(
        self,
        repo_id: str,
        state: str = "open",
        limit: int = 20
    ) -> List[GitMergeRequest]:
        """List Merge Requests from GitHub."""
        try:
            params = {"state": state, "per_page": limit}
            data = await self._github_request(repo_id, "GET", f"/repos/{self._repositories[repo_id].full_name}/pulls", params=params)
            
            mrs = []
            for pr in data:
                mr_number = pr.get("number")
                mr = await self.get_github_mr(repo_id, mr_number)
                if mr:
                    mrs.append(mr)
            
            return mrs
        except Exception as e:
            logger.error(f"Failed to list GitHub MRs: {e}")
            return []
    
    # ==================== GitLab API Integration ====================
    
    async def _gitlab_request(
        self,
        repo_id: str,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make authenticated GitLab API request."""
        repo = self._repositories.get(repo_id)
        if not repo:
            raise GitServiceError(f"Repository {repo_id} not found")
        
        token = self._gitlab_tokens.get(repo_id, "")
        if not token:
            raise GitServiceError(f"No GitLab token for repository {repo_id}")
        
        base_url = repo.api_url or self.GITLAB_API_BASE
        url = f"{base_url}/{endpoint.lstrip('/')}"
        headers = {
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
            
            if response.status_code == 401:
                raise GitServiceError(f"GitLab API unauthorized for {repo_id}")
            if response.status_code == 404:
                raise GitServiceError(f"GitLab resource not found: {endpoint}")
            if response.status_code >= 400:
                raise GitServiceError(f"GitLab API error: {response.status_code} - {response.text}")
            
            return response.json()
    
    async def get_gitlab_mr(self, repo_id: str, mr_iid: int) -> Optional[GitMergeRequest]:
        """Get a single Merge Request from GitLab."""
        try:
            project_path = self._repositories[repo_id].full_name.replace("/", "%2F")
            data = await self._gitlab_request(repo_id, "GET", f"/projects/{project_path}/merge_requests/{mr_iid}")
            
            # Get commits
            commits_data = await self._gitlab_request(repo_id, "GET", f"/projects/{project_path}/merge_requests/{mr_iid}/commits")
            
            # Get changed files
            changes_data = await self._gitlab_request(repo_id, "GET", f"/projects/{project_path}/merge_requests/{mr_iid}/changes")
            
            return self._parse_gitlab_mr(data, commits_data, changes_data)
        except Exception as e:
            logger.error(f"Failed to get GitLab MR: {e}")
            return None
    
    def _parse_gitlab_mr(
        self,
        mr_data: Dict,
        commits_data: List[Dict],
        changes_data: Dict
    ) -> GitMergeRequest:
        """Parse GitLab MR data into GitMergeRequest."""
        # Parse commits
        commits = []
        for commit in commits_data:
            commits.append(GitCommit(
                sha=commit.get("id", ""),
                message=commit.get("message", ""),
                author=commit.get("author_name", ""),
                author_email=commit.get("author_email", ""),
                committed_at=commit.get("committed_date", ""),
                changed_files=[],
            ))
        
        # Parse changed files from changes
        changes = changes_data.get("changes", [])
        changed_files = [
            GitChangedFile(
                filename=change.get("new_path", ""),
                status="modified" if not change.get("new_file") else "added",
                additions=0,  # GitLab doesn't provide this directly
                deletions=0,
            ) for change in changes
        ]
        
        # Determine status
        state = mr_data.get("state", "")
        if state == "merged":
            status = GitMRStatus.MERGED
        elif state == "closed":
            status = GitMRStatus.CLOSED
        else:
            status = GitMRStatus.OPEN
        
        return GitMergeRequest(
            mr_id=str(mr_data.get("iid", "")),
            title=mr_data.get("title", ""),
            description=mr_data.get("description", ""),
            source_branch=mr_data.get("source_branch", ""),
            target_branch=mr_data.get("target_branch", ""),
            author=mr_data.get("author", {}).get("username", ""),
            status=status,
            web_url=mr_data.get("web_url", ""),
            created_at=mr_data.get("created_at", ""),
            updated_at=mr_data.get("updated_at", ""),
            commits=commits,
            changed_files=changed_files,
        )
    
    async def list_gitlab_mrs(
        self,
        repo_id: str,
        state: str = "opened",
        limit: int = 20
    ) -> List[GitMergeRequest]:
        """List Merge Requests from GitLab."""
        try:
            project_path = self._repositories[repo_id].full_name.replace("/", "%2F")
            params = {"state": state, "per_page": limit}
            data = await self._gitlab_request(repo_id, "GET", f"/projects/{project_path}/merge_requests", params=params)
            
            mrs = []
            for mr_info in data:
                mr_iid = mr_info.get("iid")
                mr = await self.get_gitlab_mr(repo_id, mr_iid)
                if mr:
                    mrs.append(mr)
            
            return mrs
        except Exception as e:
            logger.error(f"Failed to list GitLab MRs: {e}")
            return []
    
    # ==================== Webhook Handling ====================
    
    def verify_github_webhook_signature(
        self,
        repo_id: str,
        payload: bytes,
        signature: str
    ) -> bool:
        """
        Verify GitHub webhook signature.
        
        GitHub uses HMAC-SHA256 signature in X-Hub-Signature-256 header.
        """
        repo = self._repositories.get(repo_id)
        if not repo or not repo.webhook_secret:
            logger.warning(f"No webhook secret configured for repo {repo_id}")
            return True  # Skip verification if no secret configured
        
        expected_signature = "sha256=" + hmac.new(
            repo.webhook_secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    
    def verify_gitlab_webhook_token(
        self,
        repo_id: str,
        token: str
    ) -> bool:
        """
        Verify GitLab webhook token.
        
        GitLab uses a simple token in X-Gitlab-Token header.
        """
        repo = self._repositories.get(repo_id)
        if not repo or not repo.webhook_secret:
            logger.warning(f"No webhook secret configured for repo {repo_id}")
            return True
        
        return hmac.compare_digest(token, repo.webhook_secret)
    
    async def handle_github_webhook_event(
        self,
        repo_id: str,
        event_type: str,
        payload: Dict
    ) -> Optional[GitMergeRequest]:
        """
        Handle GitHub webhook event.
        
        Returns GitMergeRequest if it's a pull_request event.
        """
        if event_type == "pull_request":
            mr_data = payload.get("pull_request", {})
            action = payload.get("action", "")
            
            # Only process certain actions
            if action not in ["opened", "synchronize", "closed", "reopened"]:
                logger.debug(f"Ignoring PR action: {action}")
                return None
            
            # Parse the MR data
            mr = self._parse_github_mr(mr_data, [], payload.get("changes", []))
            return mr
        
        elif event_type == "push":
            # Push event - could extract MR from branch name
            logger.info(f"Push event received for repo {repo_id}")
            return None
        
        else:
            logger.debug(f"Ignoring GitHub event type: {event_type}")
            return None
    
    async def handle_gitlab_webhook_event(
        self,
        repo_id: str,
        event_type: str,
        payload: Dict
    ) -> Optional[GitMergeRequest]:
        """
        Handle GitLab webhook event.
        
        Returns GitMergeRequest if it's a merge_request event.
        """
        if event_type == "merge_request":
            obj_attrs = payload.get("object_attributes", {})
            action = obj_attrs.get("action", "")
            
            # Only process certain actions
            if action not in ["open", "update", "merge", "close"]:
                logger.debug(f"Ignoring MR action: {action}")
                return None
            
            mr_iid = obj_attrs.get("iid")
            if mr_iid:
                mr = await self.get_gitlab_mr(repo_id, mr_iid)
                return mr
        
        elif event_type == "push":
            logger.info(f"Push event received for repo {repo_id}")
            return None
        
        else:
            logger.debug(f"Ignoring GitLab event type: {event_type}")
            return None
    
    # ==================== Module Mapping ====================
    
    def register_module_mapping(
        self,
        module_name: str,
        file_patterns: List[str],
        related_requirements: List[str] = [],
        test_case_ids: List[str] = []
    ) -> ModuleMapping:
        """
        Register a module to test case mapping.
        
        Args:
            module_name: Module name (e.g., "ICC-AEB", "ICC-LCC")
            file_patterns: Regex patterns to match file paths
            related_requirements: Related requirement IDs
            test_case_ids: Associated test case IDs
        """
        import re
        
        mapping = ModuleMapping(
            module_name=module_name,
            file_patterns=file_patterns,
            related_requirements=related_requirements,
            test_case_ids=test_case_ids,
        )
        
        # Check if module already exists
        for i, existing in enumerate(self._module_mappings):
            if existing.module_name == module_name:
                self._module_mappings[i] = mapping
                logger.info(f"Updated module mapping: {module_name}")
                return mapping
        
        self._module_mappings.append(mapping)
        logger.info(f"Registered module mapping: {module_name}")
        return mapping
    
    def match_changed_files_to_modules(
        self,
        changed_files: List[GitChangedFile]
    ) -> List[ChangeCaseMatch]:
        """
        Match changed files to modules based on file patterns.
        
        Args:
            changed_files: List of changed files from a MR
        
        Returns:
            List of ChangeCaseMatch results
        """
        import re
        matches = []
        
        for file in changed_files:
            filename = file.filename
            
            for mapping in self._module_mappings:
                for pattern in mapping.file_patterns:
                    try:
                        if re.match(pattern, filename) or pattern in filename:
                            # Found a match
                            match = ChangeCaseMatch(
                                changed_file=filename,
                                matched_module=mapping.module_name,
                                matched_cases=mapping.test_case_ids.copy(),
                                match_confidence=0.8 if re.match(pattern, filename) else 0.6,
                            )
                            matches.append(match)
                            break
                    except re.error:
                        # Invalid regex, try simple contains
                        if pattern in filename:
                            match = ChangeCaseMatch(
                                changed_file=filename,
                                matched_module=mapping.module_name,
                                matched_cases=mapping.test_case_ids.copy(),
                                match_confidence=0.5,
                            )
                            matches.append(match)
                            break
        
        return matches
    
    def get_module_mapping(self, module_name: str) -> Optional[ModuleMapping]:
        """Get module mapping by name."""
        for mapping in self._module_mappings:
            if mapping.module_name == module_name:
                return mapping
        return None
    
    def list_module_mappings(self) -> List[ModuleMapping]:
        """List all module mappings."""
        return self._module_mappings.copy()


# Global singleton instance
_git_service: Optional[GitService] = None


def get_git_service() -> GitService:
    """Get the global Git service instance."""
    global _git_service
    if _git_service is None:
        _git_service = GitService()
    return _git_service
