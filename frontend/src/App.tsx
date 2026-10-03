import { useEffect, useState, useCallback, type ChangeEvent, type FormEvent } from 'react';
import {
  fetchHealth,
  fetchCurrentFolder,
  selectFolder,
  clearFolder,
  startScan,
  type HealthCheckResult,
  type FolderStatusResponse,
  type ScanResult,
  findDuplicates,
  type DuplicateResult,
  extractContent,
  type ExtractionResult,
  analyzeFiles,
  type AnalysisSummary,
  searchFiles,
  findSimilarFiles,
  type SearchResponse,
  type SimilarFilesResponse,
  generateOrganizationPlan,
  getOrganizationPlan,
  type OrganizationPlan,
  startAgentRun,
  cancelAgentRun,
  type AgentRun,
  // Phase 11
  getPlanApprovals,
  decideOperation,
  type PlanApprovalStatus,
  type OperationWithApproval,
  // Phase 12
  executePlan,
  getPlanExecutions,
  type PlanExecutionStatus,
  // Phase 13
  verifyPlan,
  type PlanVerificationStatus,
} from './services/api';

export default function App() {
  // Phase 0 Health state
  const [healthResult, setHealthResult] = useState<HealthCheckResult>({
    status: 'checking',
    checkedAt: new Date().toLocaleTimeString(),
  });
  const [isRefreshingHealth, setIsRefreshingHealth] = useState<boolean>(false);
  const [autoPollHealth, setAutoPollHealth] = useState<boolean>(true);

  // Phase 1 Folder Access state
  const [folderStatus, setFolderStatus] = useState<FolderStatusResponse>({
    is_configured: false,
    authorized_root: null,
    message: 'Checking authorized root...',
  });
  const [candidatePath, setCandidatePath] = useState<string>('');
  const [isSubmittingFolder, setIsSubmittingFolder] = useState<boolean>(false);
  const [folderError, setFolderError] = useState<string | null>(null);
  const [folderSuccess, setFolderSuccess] = useState<string | null>(null);

  // Phase 2 Scanner state
  const [isScanning, setIsScanning] = useState<boolean>(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState<boolean>(false);
  const [showFolders, setShowFolders] = useState<boolean>(false);

  // Phase 4 Duplicates state
  const [isDetectingDuplicates, setIsDetectingDuplicates] = useState<boolean>(false);
  const [duplicateResult, setDuplicateResult] = useState<DuplicateResult | null>(null);
  const [duplicateError, setDuplicateError] = useState<string | null>(null);
  const [showDuplicates, setShowDuplicates] = useState<boolean>(false);

  // Phase 5 Extraction state
  const [isExtracting, setIsExtracting] = useState<boolean>(false);
  const [extractionResult, setExtractionResult] = useState<ExtractionResult | null>(null);
  const [extractionError, setExtractionError] = useState<string | null>(null);

  // Phase 6 Analysis state
  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);
  const [analysisResult, setAnalysisResult] = useState<AnalysisSummary | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  // Phase 7 Search state
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [similarResult, setSimilarResult] = useState<SimilarFilesResponse | null>(null);
  const [similarError, setSimilarError] = useState<string | null>(null);
  const [similarLoading, setSimilarLoading] = useState<boolean>(false);

  // Phase 8 Organization state
  const [organizationPlan, setOrganizationPlan] = useState<OrganizationPlan | null>(null);
  const [isPlanning, setIsPlanning] = useState<boolean>(false);
  const [organizationError, setOrganizationError] = useState<string | null>(null);
  const [organizationObjective, setOrganizationObjective] = useState<string>('');

  // Phase 9 Agent Controller state
  const [agentObjective, setAgentObjective] = useState<string>('Organize my files by category and structure');
  const [activeRun, setActiveRun] = useState<AgentRun | null>(null);
  const [isStartingRun, setIsStartingRun] = useState<boolean>(false);
  const [agentRunError, setAgentRunError] = useState<string | null>(null);

  // Phase 11 Approval state
  const [approvalStatus, setApprovalStatus] = useState<PlanApprovalStatus | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [decidingOp, setDecidingOp] = useState<number | null>(null); // op id being decided

  // Phase 12 Execution state
  const [executionStatus, setExecutionStatus] = useState<PlanExecutionStatus | null>(null);
  const [isExecuting, setIsExecuting] = useState<boolean>(false);
  const [executionError, setExecutionError] = useState<string | null>(null);

  // Phase 13 Verification state
  const [verificationStatus, setVerificationStatus] = useState<PlanVerificationStatus | null>(null);
  const [isVerifying, setIsVerifying] = useState<boolean>(false);
  const [verificationError, setVerificationError] = useState<string | null>(null);

  // Health check handler
  const checkHealthStatus = useCallback(async () => {
    setIsRefreshingHealth(true);
    try {
      const result = await fetchHealth();
      setHealthResult(result);
    } catch {
      setHealthResult({
        status: 'unavailable',
        error: 'Unexpected error occurred while requesting health status',
        checkedAt: new Date().toLocaleTimeString(),
      });
    } finally {
      setIsRefreshingHealth(false);
    }
  }, []);

  // Folder status handler
  const loadFolderStatus = useCallback(async () => {
    const result = await fetchCurrentFolder();
    if (result.success && result.data && 'is_configured' in result.data) {
      setFolderStatus(result.data as FolderStatusResponse);
    }
  }, []);

  useEffect(() => {
    checkHealthStatus();
    loadFolderStatus();
  }, [checkHealthStatus, loadFolderStatus]);

  useEffect(() => {
    if (!autoPollHealth) return;
    const timer = setInterval(() => {
      checkHealthStatus();
    }, 5000);
    return () => clearInterval(timer);
  }, [autoPollHealth, checkHealthStatus]);

  // Phase 12 Execution Polling
  useEffect(() => {
    if (!executionStatus || (executionStatus.in_progress_count === 0 && executionStatus.pending_count === 0)) return;
    const timer = setInterval(async () => {
      const res = await getPlanExecutions(executionStatus.plan_id);
      if (res.success && res.data) {
        setExecutionStatus(res.data);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [executionStatus]);

  const handleSelectFolderSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedPath = candidatePath.trim();
    if (!trimmedPath) {
      setFolderError('Please enter a directory path.');
      return;
    }

    setIsSubmittingFolder(true);
    setFolderError(null);
    setFolderSuccess(null);

    const result = await selectFolder(trimmedPath);
    setIsSubmittingFolder(false);

    if (result.success && result.data && 'authorized_root' in result.data) {
      setFolderSuccess(`Successfully established authorized root: ${result.data.authorized_root}`);
      setFolderStatus({
        is_configured: true,
        authorized_root: result.data.authorized_root,
        message: result.data.message,
      });
      setCandidatePath('');
      // Clear any previous scan results when root changes
      setScanResult(null);
      setScanError(null);
    } else {
      setFolderError(result.error || 'Failed to authorize the specified directory.');
    }
  };

  const handleClearFolder = async () => {
    setFolderError(null);
    setFolderSuccess(null);
    const result = await clearFolder();
    if (result.success) {
      setFolderStatus({
        is_configured: false,
        authorized_root: null,
        message: 'No authorized root directory is currently configured.',
      });
      setFolderSuccess('Authorized root has been cleared.');
      // Clear scan results when root is cleared
      setScanResult(null);
      setScanError(null);
      setDuplicateResult(null);
      setDuplicateError(null);
      setExtractionResult(null);
      setExtractionError(null);
    } else {
      setFolderError(result.error || 'Failed to clear authorized root.');
    }
  };

  // Phase 2: Scan handler
  const handleStartScan = async () => {
    setIsScanning(true);
    setScanError(null);
    setScanResult(null);
    setShowFiles(false);
    setShowFolders(false);
    setDuplicateResult(null);
    setDuplicateError(null);
    setShowDuplicates(false);
    setExtractionResult(null);
    setExtractionError(null);
    setAnalysisResult(null);
    setAnalysisError(null);

    const result = await startScan();
    setIsScanning(false);

    if (result.success && result.data) {
      setScanResult(result.data);
    } else {
      setScanError(result.error || 'Scan failed for an unknown reason.');
    }
  };

  // Phase 4: Duplicate handler
  const handleFindDuplicates = async () => {
    if (!scanResult) return;
    
    setIsDetectingDuplicates(true);
    setDuplicateError(null);
    setDuplicateResult(null);
    setShowDuplicates(true);

    const result = await findDuplicates(scanResult.scan_id);
    setIsDetectingDuplicates(false);

    if (result.success && result.data) {
      setDuplicateResult(result.data);
    } else {
      setDuplicateError(result.error || 'Duplicate detection failed.');
    }
  };

  // Phase 5: Extraction handler
  const handleExtractContent = async () => {
    if (!scanResult) return;
    
    setIsExtracting(true);
    setExtractionError(null);
    setExtractionResult(null);

    const result = await extractContent(scanResult.scan_id);
    setIsExtracting(false);

    if (result.success && result.data) {
      setExtractionResult(result.data);
    } else {
      setExtractionError(result.error || 'Content extraction failed.');
    }
  };

  const handleAnalyzeFiles = useCallback(async () => {
    if (!scanResult || isAnalyzing) return;
    
    setIsAnalyzing(true);
    setAnalysisError(null);
    setAnalysisResult(null);

    const result = await analyzeFiles(scanResult.scan_id);
    setIsAnalyzing(false);

    if (result.success && result.data) {
      setAnalysisResult(result.data);
    } else {
      setAnalysisError(result.error || 'Unknown error during analysis');
    }
  }, [scanResult, isAnalyzing]);

  const handleSearch = useCallback(async () => {
    if (!scanResult || !searchQuery.trim() || isSearching) return;
    setIsSearching(true);
    setSearchError(null);
    setSearchResult(null);
    setSimilarResult(null);
    const result = await searchFiles(scanResult.scan_id, searchQuery.trim());
    setIsSearching(false);
    if (result.success && result.data) {
      setSearchResult(result.data);
    } else {
      setSearchError(result.error || 'Search failed');
    }
  }, [scanResult, searchQuery, isSearching]);

  const handleFindSimilar = useCallback(async (fileId: number) => {
    if (!scanResult || similarLoading) return;
    setSimilarLoading(true);
    setSimilarError(null);
    setSimilarResult(null);
    const result = await findSimilarFiles(scanResult.scan_id, fileId);
    setSimilarLoading(false);
    if (result.success && result.data) {
      setSimilarResult(result.data);
    } else {
      setSimilarError(result.error || 'Similar files search failed');
    }
  }, [scanResult, similarLoading]);

  // Phase 8 Organization handler
  const handleGeneratePlan = useCallback(async () => {
    if (!scanResult || isPlanning) return;
    setIsPlanning(true);
    setOrganizationError(null);
    setApprovalStatus(null);
    setApprovalError(null);
    setExecutionStatus(null);
    setExecutionError(null);
    const result = await generateOrganizationPlan(
      scanResult.scan_id,
      organizationObjective.trim() || undefined
    );
    setIsPlanning(false);
    if (result.success && result.data) {
      setOrganizationPlan(result.data.plan);
      // Auto-load approval and execution status
      const planId = result.data.plan.plan_id;
      const approvalRes = await getPlanApprovals(planId);
      if (approvalRes.success && approvalRes.data) setApprovalStatus(approvalRes.data);
      const execRes = await getPlanExecutions(planId);
      if (execRes.success && execRes.data) setExecutionStatus(execRes.data);
    } else {
      setOrganizationError(result.error || 'Failed to generate organization plan');
    }
  }, [scanResult, isPlanning, organizationObjective]);

  // Phase 9 Agent Controller handlers
  const handleStartAgentRun = useCallback(async () => {
    if (!agentObjective.trim() || isStartingRun) return;
    setIsStartingRun(true);
    setAgentRunError(null);
    setApprovalStatus(null);
    setApprovalError(null);
    setExecutionStatus(null);
    setExecutionError(null);
    const result = await startAgentRun(agentObjective.trim(), scanResult?.scan_id);
    setIsStartingRun(false);
    if (result.success && result.data) {
      setActiveRun(result.data.run);
      // If plan was generated, fetch and display it
      if (result.data.run.plan_id) {
        const planRes = await getOrganizationPlan(result.data.run.plan_id);
        if (planRes.success && planRes.data) {
          setOrganizationPlan(planRes.data.plan);
        }
        // Auto-load approval and execution status for the agent-generated plan
        const approvalRes = await getPlanApprovals(result.data.run.plan_id);
        if (approvalRes.success && approvalRes.data) setApprovalStatus(approvalRes.data);
        const execRes = await getPlanExecutions(result.data.run.plan_id);
        if (execRes.success && execRes.data) setExecutionStatus(execRes.data);
      }
    } else {
      setAgentRunError(result.error || 'Failed to start agent run');
    }
  }, [agentObjective, isStartingRun, scanResult]);

  // Phase 11 — record an approval decision for a single operation
  const handleDecideOperation = useCallback(async (
    planId: string,
    operationId: number,
    decision: 'APPROVED' | 'REJECTED',
  ) => {
    setDecidingOp(operationId);
    setApprovalError(null);
    const result = await decideOperation(planId, operationId, decision);
    setDecidingOp(null);
    if (result.success && result.data) {
      // Refresh the full plan approval status to get updated counts
      const refreshed = await getPlanApprovals(planId);
      if (refreshed.success && refreshed.data) setApprovalStatus(refreshed.data);
    } else {
      setApprovalError(result.error || 'Failed to record decision');
    }
  }, []);

  // Phase 12 — execute approved operations
  const handleExecutePlan = useCallback(async () => {
    if (!organizationPlan || isExecuting) return;
    setIsExecuting(true);
    setExecutionError(null);
    const result = await executePlan(organizationPlan.plan_id);
    if (result.success && result.data) {
      setExecutionStatus(result.data);
    } else {
      setExecutionError(result.error || 'Failed to execute plan');
    }
    setIsExecuting(false);
  }, [organizationPlan, isExecuting]);

  // Phase 13 — verify executed operations
  const handleVerifyPlan = useCallback(async () => {
    if (!organizationPlan || isVerifying) return;
    setIsVerifying(true);
    setVerificationError(null);
    const result = await verifyPlan(organizationPlan.plan_id);
    if (result.success && result.data) {
      setVerificationStatus(result.data);
    } else {
      setVerificationError(result.error || 'Failed to verify plan');
    }
    setIsVerifying(false);
  }, [organizationPlan, isVerifying]);

  const handleCancelAgentRun = useCallback(async () => {
    if (!activeRun) return;
    const result = await cancelAgentRun(activeRun.run_id);
    if (result.success && result.data) {
      setActiveRun(result.data.run);
    } else {
      setAgentRunError(result.error || 'Failed to cancel agent run');
    }
  }, [activeRun]);

  // Browser folder input helper
  const handleDirectoryPickerChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      const firstRelative = files[0].webkitRelativePath;
      const folderName = firstRelative.split('/')[0];
      if (folderName) {
        setFolderSuccess(
          `Directory "${folderName}" selected from browser. Ensure full local path is specified below if required by your OS.`
        );
      }
    }
  };

  const getStatusBadge = () => {
    switch (healthResult.status) {
      case 'connected':
        return <span className="badge badge-success">● Connected</span>;
      case 'checking':
        return <span className="badge badge-warning">◌ Checking...</span>;
      case 'unavailable':
        return <span className="badge badge-danger">✕ Backend Unavailable</span>;
      case 'malformed':
        return <span className="badge badge-warning">⚠ Malformed Response</span>;
    }
  };

  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };

  const formatDate = (iso: string): string => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  return (
    <div className="container">
      <header className="header">
        <div className="brand">
          <div className="brand-logo">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              <polyline points="12 11 12 17" />
              <polyline points="9 14 12 17 15 14" />
            </svg>
          </div>
          <div>
            <h1 className="brand-title">SmartSort</h1>
            <p className="brand-subtitle">Local-First AI File &amp; Folder Organization Agent</p>
          </div>
        </div>
        <div className="phase-pill">
          <span className="phase-tag">Phase 4</span>
          <span className="phase-label">Duplicate Detection</span>
        </div>
        <div className="phase-pill" style={{ marginLeft: '0.5rem' }}>
          <span className="phase-tag" style={{ background: '#6366f1' }}>Phase 5</span>
          <span className="phase-label">Document Extraction</span>
        </div>
        <div className="phase-pill" style={{ marginLeft: '0.5rem' }}>
          <span className="phase-tag" style={{ background: '#10b981' }}>Phase 8</span>
          <span className="phase-label">Organization Engine</span>
        </div>
        <div className="phase-pill" style={{ marginLeft: '0.5rem' }}>
          <span className="phase-tag" style={{ background: '#8b5cf6' }}>Phase 9</span>
          <span className="phase-label">Agent Controller</span>
        </div>
      </header>

      <main className="main-content">
        <section className="architecture-banner">
          <div className="banner-icon">🔒</div>
          <div>
            <strong>Filesystem Security Boundary:</strong> The backend is the sole authority for filesystem access.
            The scanner operates strictly within the authorized root. No scan path can be supplied by the frontend.
            All operations are <strong>read-only</strong> — no files are created, modified, or deleted.
          </div>
        </section>

        {/* Phase 1: Local Folder Access Card */}
        <section className="folder-card card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Authorized Root Directory</h2>
              <p className="card-subtitle">Select and establish the security root for all SmartSort operations.</p>
            </div>
            {folderStatus.is_configured ? (
              <span className="badge badge-success">● Authorized Root Active</span>
            ) : (
              <span className="badge badge-warning">○ No Root Configured</span>
            )}
          </div>

          <div className="card-body">
            {folderStatus.is_configured && folderStatus.authorized_root ? (
              <div className="active-root-box">
                <div className="active-root-header">
                  <span className="active-root-label">Current Canonical Root:</span>
                  <button
                    id="clear-root-btn"
                    className="btn btn-secondary btn-sm"
                    onClick={handleClearFolder}
                  >
                    Clear / Change Root
                  </button>
                </div>
                <code className="active-root-path">{folderStatus.authorized_root}</code>
                <p className="active-root-hint">
                  All future operations will be strictly constrained to this directory and its descendants.
                </p>
              </div>
            ) : (
              <form onSubmit={handleSelectFolderSubmit} className="folder-form">
                <div className="form-group">
                  <label htmlFor="folder-path-input" className="form-label">
                    Enter or paste local directory path:
                  </label>
                  <div className="input-group">
                    <input
                      id="folder-path-input"
                      type="text"
                      className="form-input"
                      placeholder="e.g. C:\Users\Username\Downloads or /home/user/documents"
                      value={candidatePath}
                      onChange={(e) => setCandidatePath(e.target.value)}
                      disabled={isSubmittingFolder}
                    />
                    <button
                      id="submit-folder-btn"
                      type="submit"
                      className="btn btn-primary"
                      disabled={isSubmittingFolder || !candidatePath.trim()}
                    >
                      {isSubmittingFolder ? 'Validating...' : 'Authorize Folder'}
                    </button>
                  </div>
                </div>

                <div className="picker-alternative">
                  <span className="text-small text-muted">Or browse folder using file picker:</span>
                  <label className="btn btn-secondary btn-sm picker-label">
                    Choose Folder...
                    <input
                      type="file"
                      // @ts-expect-error webkitdirectory is standard in browser engines
                      webkitdirectory=""
                      directory=""
                      onChange={handleDirectoryPickerChange}
                      style={{ display: 'none' }}
                    />
                  </label>
                </div>
              </form>
            )}

            {folderSuccess && (
              <div className="alert-box alert-success">
                <span className="alert-icon">✓</span>
                <div>{folderSuccess}</div>
              </div>
            )}

            {folderError && (
              <div className="alert-box alert-danger">
                <span className="alert-icon">✕</span>
                <div>{folderError}</div>
              </div>
            )}
          </div>
        </section>

        {/* Phase 9: Agent Controller Card */}
        <section className="agent-card">
          <div className="agent-header">
            <div>
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>🤖 Agent Controller</h2>
              <p className="text-small text-muted" style={{ margin: '4px 0 0 0' }}>
                Autonomous workflow orchestrator: Scan → Analyze → Plan → Waiting for Approval.
              </p>
            </div>
            {activeRun && (
              <span className={`agent-status-badge ${
                activeRun.state === 'WAITING_FOR_APPROVAL' ? 'state-waiting' :
                activeRun.state === 'CANCELLED' ? 'state-cancelled' :
                activeRun.state === 'ERROR' ? 'state-error' :
                'state-planning'
              }`}>
                {activeRun.state === 'WAITING_FOR_APPROVAL' ? '● Waiting for Approval' :
                 activeRun.state === 'CANCELLED' ? '○ Cancelled' :
                 activeRun.state === 'ERROR' ? '✕ Error' :
                 `◌ ${activeRun.state}`}
              </span>
            )}
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
            <input
              id="agent-objective-input"
              type="text"
              className="text-input"
              placeholder="Enter user objective (e.g. Organize my documents by category)"
              value={agentObjective}
              onChange={(e) => setAgentObjective(e.target.value)}
              disabled={isStartingRun || !folderStatus.is_configured}
              style={{ flex: 1, minWidth: '280px' }}
            />
            <button
              id="start-agent-btn"
              className="btn btn-primary"
              onClick={handleStartAgentRun}
              disabled={isStartingRun || !folderStatus.is_configured || !agentObjective.trim()}
            >
              {isStartingRun ? (
                <><span className="scan-spinner">◌</span> Orchestrating Workflow…</>
              ) : (
                <><span>⚡</span> {activeRun ? 'Start New Agent Run' : 'Start Agent Run'}</>
              )}
            </button>
            {activeRun && (activeRun.state === 'SCANNING' || activeRun.state === 'ANALYZING' || activeRun.state === 'PLANNING' || activeRun.state === 'WAITING_FOR_APPROVAL') && (
              <button
                id="cancel-agent-btn"
                className="btn btn-secondary"
                onClick={handleCancelAgentRun}
              >
                Cancel Run
              </button>
            )}
          </div>

          {!folderStatus.is_configured && (
            <p className="text-small text-muted" style={{ color: 'var(--status-warning)' }}>
              ⚠️ Configure an authorized directory above before running the agent.
            </p>
          )}

          {agentRunError && (
            <div className="alert-box alert-danger" style={{ marginBottom: '1rem' }}>
              <span className="alert-icon">✕</span><div>{agentRunError}</div>
            </div>
          )}

          {activeRun && (
            <div style={{ background: 'rgba(0, 0, 0, 0.25)', border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '6px' }}>
                <span className="text-muted">Run ID: <code style={{ color: '#c7d2fe' }}>{activeRun.run_id}</code></span>
                <span className="text-muted">Progress: <strong>{Math.round(activeRun.progress * 100)}%</strong></span>
              </div>

              <div className="agent-progress-bar-bg">
                <div className="agent-progress-bar-fill" style={{ width: `${Math.round(activeRun.progress * 100)}%` }} />
              </div>

              <div style={{ fontSize: '0.85rem', color: '#e2e8f0', marginTop: '6px' }}>
                <strong>Current Step:</strong> {activeRun.current_step || 'In progress'}
              </div>

              {activeRun.state === 'WAITING_FOR_APPROVAL' && (
                <div className="agent-waiting-banner">
                  <div className="agent-waiting-title">✓ Planning complete — waiting for your approval.</div>
                  <p className="text-small" style={{ color: '#d1fae5', margin: '4px 0 8px 0' }}>
                    No files have been modified. Review the generated organization proposals below.
                  </p>
                  {activeRun.plan_id && (
                    <span className="text-small" style={{ opacity: 0.85 }}>
                      Linked Plan ID: <code style={{ color: '#a7f3d0' }}>{activeRun.plan_id}</code>
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        {/* Phase 2: Filesystem Scanner Card */}
        <section className="scan-card card">
          <div className="card-header">
            <div>
              <h2 className="card-title">Filesystem Scanner</h2>
              <p className="card-subtitle">
                Recursively discover files and folders within the authorized root.
              </p>
            </div>
            {scanResult ? (
              <span className={`badge ${scanResult.status === 'completed' ? 'badge-success' : 'badge-danger'}`}>
                {scanResult.status === 'completed' ? '✓ Scan Complete' : '✕ Scan Failed'}
              </span>
            ) : isScanning ? (
              <span className="badge badge-warning">◌ Scanning...</span>
            ) : (
              <span className="badge badge-warning">○ Not Scanned</span>
            )}
          </div>

          <div className="card-body">
            {!folderStatus.is_configured && (
              <div className="scan-no-root">
                <div className="scan-no-root-icon">📂</div>
                <p>No authorized root is configured. Select a root directory above before scanning.</p>
              </div>
            )}

            {folderStatus.is_configured && (
              <>
                <div className="scan-action-row">
                  <div className="scan-target">
                    <span className="scan-target-label">Scan target:</span>
                    <code className="scan-target-path">{scanResult?.root_display ?? folderStatus.authorized_root?.split(/[/\\]/).pop() ?? '—'}</code>
                  </div>
                  <button
                    id="start-scan-btn"
                    className="btn btn-primary scan-btn"
                    onClick={handleStartScan}
                    disabled={isScanning}
                  >
                    {isScanning ? (
                      <><span className="scan-spinner">◌</span> Scanning...</>
                    ) : (
                      <><span>⟳</span> {scanResult ? 'Re-Scan' : 'Start Scan'}</>
                    )}
                  </button>
                </div>

                {scanError && (
                  <div className="alert-box alert-danger">
                    <span className="alert-icon">✕</span>
                    <div>{scanError}</div>
                  </div>
                )}

                {scanResult && (
                  <>
                    {/* Stats Grid */}
                    <div className="scan-stats-grid">
                      <div className="stat-box stat-box-files">
                        <div className="stat-value">{scanResult.file_count.toLocaleString()}</div>
                        <div className="stat-label">Files</div>
                      </div>
                      <div className="stat-box stat-box-folders">
                        <div className="stat-value">{scanResult.directory_count.toLocaleString()}</div>
                        <div className="stat-label">Folders</div>
                      </div>
                      {scanResult.skip_count > 0 && (
                        <div className="stat-box stat-box-skips">
                          <div className="stat-value">{scanResult.skip_count.toLocaleString()}</div>
                          <div className="stat-label">Skipped</div>
                        </div>
                      )}
                    </div>

                    {/* Scan meta */}
                    <div className="scan-meta">
                      <div className="scan-meta-row">
                        <span className="scan-meta-label">Scan ID</span>
                        <code className="scan-meta-value scan-id-value">{scanResult.scan_id}</code>
                      </div>
                      <div className="scan-meta-row">
                        <span className="scan-meta-label">Scanned at</span>
                        <span className="scan-meta-value">{formatDate(scanResult.scanned_at)}</span>
                      </div>
                      <div className="scan-meta-row">
                        <span className="scan-meta-label">Status</span>
                        <span className={`scan-meta-value ${scanResult.status === 'completed' ? 'text-success' : 'text-danger'}`}>
                          {scanResult.status}
                        </span>
                      </div>
                    </div>

                    {scanResult.error && (
                      <div className="alert-box alert-danger">
                        <span className="alert-icon">✕</span>
                        <div>{scanResult.error}</div>
                      </div>
                    )}

                    {/* Folders list */}
                    {scanResult.directory_count > 0 && (
                      <div className="entry-section">
                        <button
                          className="entry-section-toggle"
                          onClick={() => setShowFolders(v => !v)}
                          aria-expanded={showFolders}
                        >
                          <span className="entry-section-icon">📁</span>
                          <span className="entry-section-title">
                            Folders ({scanResult.directory_count.toLocaleString()})
                          </span>
                          <span className="entry-section-chevron">{showFolders ? '▲' : '▼'}</span>
                        </button>
                        {showFolders && (
                          <div className="entry-list" role="list">
                            {scanResult.directories.map((dir) => (
                              <div key={dir.relative_path} className="entry-item entry-item-dir" role="listitem">
                                <span className="entry-icon">📁</span>
                                <span className="entry-path">{dir.relative_path}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Files list */}
                    {scanResult.file_count > 0 && (
                      <div className="entry-section">
                        <button
                          className="entry-section-toggle"
                          onClick={() => setShowFiles(v => !v)}
                          aria-expanded={showFiles}
                        >
                          <span className="entry-section-icon">📄</span>
                          <span className="entry-section-title">
                            Files ({scanResult.file_count.toLocaleString()})
                          </span>
                          <span className="entry-section-chevron">{showFiles ? '▲' : '▼'}</span>
                        </button>
                        {showFiles && (
                          <div className="entry-list" role="list">
                            {scanResult.files.map((file) => (
                              <div key={file.relative_path} className="entry-item entry-item-file" role="listitem">
                                <span className="entry-icon">
                                  {file.extension === '.pdf' ? '📕' :
                                   file.extension === '.txt' || file.extension === '.md' ? '📝' :
                                   ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg'].includes(file.extension) ? '🖼️' :
                                   ['.mp3', '.wav', '.flac', '.aac'].includes(file.extension) ? '🎵' :
                                   ['.mp4', '.mov', '.avi', '.mkv'].includes(file.extension) ? '🎬' :
                                   ['.zip', '.rar', '.7z', '.tar', '.gz'].includes(file.extension) ? '📦' :
                                   ['.js', '.ts', '.py', '.java', '.cpp', '.c', '.cs', '.go', '.rs'].includes(file.extension) ? '💻' :
                                   '📄'}
                                </span>
                                <div className="entry-info">
                                  <span className="entry-path">{file.relative_path}</span>
                                  <span className="entry-meta">
                                    {formatBytes(file.size_bytes)}
                                    {file.extension && <span className="entry-ext">{file.extension}</span>}
                                    {file.mime_type && <span className="entry-mime">{file.mime_type}</span>}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Phase 4: Duplicate Detection UI */}
                    {scanResult.file_count > 0 && scanResult.status === 'completed' && (
                      <div className="entry-section duplicates-section">
                        <div className="scan-action-row" style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                          <div>
                            <strong>Find Exact Duplicates</strong>
                            <p className="text-small text-muted">Cryptographically hash files to find strictly identical contents.</p>
                          </div>
                          <button
                            className="btn btn-secondary scan-btn"
                            onClick={handleFindDuplicates}
                            disabled={isDetectingDuplicates}
                          >
                            {isDetectingDuplicates ? (
                              <><span className="scan-spinner">◌</span> Hashing...</>
                            ) : (
                              <><span>🔍</span> {duplicateResult ? 'Re-Detect' : 'Find Duplicates'}</>
                            )}
                          </button>
                        </div>

                        {duplicateError && (
                          <div className="alert-box alert-danger" style={{ marginTop: '1rem' }}>
                            <span className="alert-icon">✕</span>
                            <div>{duplicateError}</div>
                          </div>
                        )}

                        {duplicateResult && (
                          <div style={{ marginTop: '1rem' }}>
                            <button
                              className="entry-section-toggle"
                              onClick={() => setShowDuplicates(v => !v)}
                              aria-expanded={showDuplicates}
                              style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)' }}
                            >
                              <span className="entry-section-icon">👯</span>
                              <span className="entry-section-title">
                                Duplicate Groups ({duplicateResult.total_groups.toLocaleString()})
                              </span>
                              <span className="entry-section-chevron">{showDuplicates ? '▲' : '▼'}</span>
                            </button>
                            
                            {showDuplicates && duplicateResult.total_groups === 0 && (
                              <div className="scan-empty" style={{ padding: '1.5rem', background: 'var(--bg-page)', marginTop: '0.5rem', borderRadius: '4px' }}>
                                <p>No exact duplicate files were found in this scan.</p>
                              </div>
                            )}

                            {showDuplicates && duplicateResult.total_groups > 0 && (
                              <div className="entry-list" role="list" style={{ gap: '1rem' }}>
                                {duplicateResult.groups.map((group, idx) => (
                                  <div key={group.hash_digest} className="duplicate-group" style={{ background: 'var(--bg-page)', borderRadius: '6px', border: '1px solid var(--border-color)', overflow: 'hidden' }}>
                                    <div style={{ padding: '0.75rem 1rem', background: 'var(--bg-card)', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                      <strong>Group {idx + 1}</strong>
                                      <span className="text-small text-muted">
                                        {group.files.length} files • {formatBytes(group.size_bytes)} each
                                      </span>
                                    </div>
                                    <div style={{ padding: '0.5rem' }}>
                                      {group.files.map(file => (
                                        <div key={file.file_id} className="entry-item entry-item-file" style={{ border: 'none', background: 'transparent' }}>
                                          <span className="entry-icon">📄</span>
                                          <div className="entry-info">
                                            <span className="entry-path" style={{ fontFamily: 'monospace' }}>{file.relative_path}</span>
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Phase 5: Document Extraction UI */}
                    {scanResult.file_count > 0 && scanResult.status === 'completed' && (
                      <div className="entry-section extraction-section">
                        <div className="scan-action-row" style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                          <div>
                            <strong>Extract Document Text</strong>
                            <p className="text-small text-muted">Read text from PDFs, DOCXs, and TXTs for analysis.</p>
                          </div>
                          <button
                            className="btn btn-primary scan-btn"
                            onClick={handleExtractContent}
                            disabled={isExtracting}
                          >
                            {isExtracting ? (
                              <><span className="scan-spinner">◌</span> Extracting...</>
                            ) : (
                              <><span>📄</span> {extractionResult ? 'Re-Extract' : 'Extract Text'}</>
                            )}
                          </button>
                        </div>

                        {extractionError && (
                          <div className="alert-box alert-danger" style={{ marginTop: '1rem' }}>
                            <span className="alert-icon">✕</span>
                            <div>{extractionError}</div>
                          </div>
                        )}

                        {extractionResult && (
                          <div style={{ marginTop: '1rem' }}>
                            <div className="scan-stats-grid" style={{ marginBottom: '1rem' }}>
                              <div className="stat-box" style={{ borderColor: 'var(--success-color)' }}>
                                <div className="stat-value text-success">{extractionResult.successful}</div>
                                <div className="stat-label">Successful</div>
                              </div>
                              <div className="stat-box" style={{ borderColor: 'var(--warning-color)' }}>
                                <div className="stat-value text-warning">{extractionResult.empty}</div>
                                <div className="stat-label">Empty</div>
                              </div>
                              <div className="stat-box" style={{ borderColor: 'var(--danger-color)' }}>
                                <div className="stat-value text-danger">{extractionResult.failed}</div>
                                <div className="stat-label">Failed</div>
                              </div>
                              <div className="stat-box" style={{ borderColor: 'var(--border-color)' }}>
                                <div className="stat-value text-muted">{extractionResult.unsupported}</div>
                                <div className="stat-label">Unsupported</div>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Phase 6: File Analysis UI */}
                    {scanResult.file_count > 0 && scanResult.status === 'completed' && (
                      <div className="entry-section analysis-section">
                        <div className="scan-action-row" style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                          <div>
                            <strong>Analyze Files</strong>
                            <p className="text-small text-muted">Generate insights on category, size, age, and content.</p>
                          </div>
                          <button
                            className="btn btn-primary scan-btn"
                            onClick={handleAnalyzeFiles}
                            disabled={isAnalyzing}
                          >
                            {isAnalyzing ? (
                              <><span className="scan-spinner">◌</span> Analyzing...</>
                            ) : (
                              <><span>📊</span> {analysisResult ? 'Re-Analyze' : 'Analyze Files'}</>
                            )}
                          </button>
                        </div>

                        {analysisError && (
                          <div className="alert-box alert-danger" style={{ marginTop: '1rem' }}>
                            <span className="alert-icon">✕</span>
                            <div>{analysisError}</div>
                          </div>
                        )}

                        {analysisResult && (
                          <div style={{ marginTop: '1rem' }}>
                            <div className="scan-stats-grid" style={{ marginBottom: '1rem' }}>
                              <div className="stat-box">
                                <div className="stat-value text-primary">{analysisResult.total_analyzed}</div>
                                <div className="stat-label">Analyzed</div>
                              </div>
                              <div className="stat-box">
                                <div className="stat-value text-accent">{formatBytes(analysisResult.total_size_bytes)}</div>
                                <div className="stat-label">Total Size</div>
                              </div>
                              <div className="stat-box">
                                <div className="stat-value">{Object.keys(analysisResult.categories).length}</div>
                                <div className="stat-label">Categories</div>
                              </div>
                              <div className="stat-box">
                                <div className="stat-value">{analysisResult.content_analyzed}</div>
                                <div className="stat-label">W/ Content</div>
                              </div>
                            </div>
                            
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '1rem' }}>
                              <div style={{ background: 'var(--bg-card)', padding: '1rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                                <h4 style={{ margin: '0 0 0.5rem 0' }}>Categories</h4>
                                {Object.entries(analysisResult.categories).sort((a,b)=>b[1]-a[1]).map(([cat, count]) => (
                                  <div key={cat} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '4px' }}>
                                    <span style={{ textTransform: 'capitalize' }}>{cat}</span>
                                    <strong>{count}</strong>
                                  </div>
                                ))}
                              </div>
                              
                              <div style={{ background: 'var(--bg-card)', padding: '1rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                                <h4 style={{ margin: '0 0 0.5rem 0' }}>Age Distribution</h4>
                                {Object.entries(analysisResult.age_buckets).sort((a,b)=>b[1]-a[1]).map(([age, count]) => (
                                  <div key={age} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem', marginBottom: '4px' }}>
                                    <span style={{ textTransform: 'capitalize' }}>{age.replace('_', ' ')}</span>
                                    <strong>{count}</strong>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Phase 7: Search UI */}
                    {scanResult.file_count > 0 && scanResult.status === 'completed' && (
                      <div className="entry-section" style={{ marginTop: '1.5rem', borderTop: '1px solid var(--border-color)', paddingTop: '1rem' }}>
                        <strong style={{ display: 'block', marginBottom: '0.5rem' }}>🔍 Search Files</strong>
                        <p className="text-small text-muted" style={{ marginBottom: '0.75rem' }}>Search by filename, folder, or document content (TF-IDF ranked).</p>
                        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                          <input
                            id="search-query-input"
                            type="text"
                            className="text-input"
                            placeholder="e.g. java notes, project report…"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                            style={{ flex: 1 }}
                          />
                          <button
                            id="search-btn"
                            className="btn btn-primary"
                            onClick={handleSearch}
                            disabled={isSearching || !searchQuery.trim()}
                          >
                            {isSearching ? <><span className="scan-spinner">◌</span> Searching…</> : 'Search'}
                          </button>
                        </div>

                        {searchError && (
                          <div className="alert-box alert-danger" style={{ marginBottom: '1rem' }}>
                            <span className="alert-icon">✕</span><div>{searchError}</div>
                          </div>
                        )}

                        {searchResult && (
                          <div>
                            <p className="text-small text-muted" style={{ marginBottom: '0.75rem' }}>
                              {searchResult.total_results} result{searchResult.total_results !== 1 ? 's' : ''} for <em>"{searchResult.query}"</em>
                            </p>
                            {searchResult.results.length === 0 && (
                              <p className="text-small text-muted">No matches found.</p>
                            )}
                            {searchResult.results.map((r) => (
                              <div key={r.file_id} style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem', marginBottom: '0.5rem' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                  <div>
                                    <strong>{r.name}</strong>
                                    {r.category && <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem', opacity: 0.6, textTransform: 'capitalize' }}>{r.category}</span>}
                                    <div className="text-small text-muted">{r.relative_path}</div>
                                    <div style={{ marginTop: '0.25rem', fontSize: '0.75rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                                      {r.matched_fields.map(f => (
                                        <span key={f} style={{ background: 'var(--accent-color-dim, rgba(99,179,237,0.15))', padding: '1px 6px', borderRadius: '4px' }}>{f}</span>
                                      ))}
                                      {r.matched_terms.slice(0, 5).map(t => (
                                        <span key={t} style={{ fontFamily: 'monospace', opacity: 0.7 }}>{t}</span>
                                      ))}
                                    </div>
                                  </div>
                                  <div style={{ textAlign: 'right', flexShrink: 0, marginLeft: '1rem' }}>
                                    <div style={{ fontSize: '0.8rem', opacity: 0.8 }}>relevance</div>
                                    <strong style={{ color: 'var(--accent-color, #63b3ed)' }}>{(r.relevance_score * 100).toFixed(0)}%</strong>
                                    <div style={{ marginTop: '0.25rem' }}>
                                      <button
                                        className="btn btn-secondary"
                                        style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                                        onClick={() => handleFindSimilar(r.file_id)}
                                        disabled={similarLoading}
                                      >
                                        Similar
                                      </button>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}

                        {similarError && (
                          <div className="alert-box alert-danger" style={{ marginTop: '0.5rem' }}>
                            <span className="alert-icon">✕</span><div>{similarError}</div>
                          </div>
                        )}

                        {similarResult && (
                          <div style={{ marginTop: '1rem', padding: '1rem', background: 'var(--bg-card)', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
                            <strong>Similar to: {similarResult.target_name}</strong>
                            {similarResult.similar_files.length === 0 ? (
                              <p className="text-small text-muted">No similar files found (insufficient content).</p>
                            ) : (
                              similarResult.similar_files.map((s) => (
                                <div key={s.file_id} style={{ display: 'flex', justifyContent: 'space-between', padding: '0.4rem 0', borderBottom: '1px solid var(--border-color)' }}>
                                  <span className="text-small">{s.relative_path}</span>
                                  <span className="text-small" style={{ opacity: 0.7 }}>{(s.similarity_score * 100).toFixed(0)}% similar</span>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Phase 8: Organization Engine UI */}
                    {scanResult.file_count > 0 && scanResult.status === 'completed' && (
                      <div className="plan-section">
                        <div className="scan-action-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', marginBottom: '1rem' }}>
                          <div>
                            <strong style={{ fontSize: '1.05rem' }}>🗂️ Proposed Organization Plan (Phase 8)</strong>
                            <p className="text-small text-muted">Generate a deterministic, read-only organization plan based on file analysis.</p>
                          </div>
                          <button
                            id="generate-plan-btn"
                            className="btn btn-primary"
                            onClick={handleGeneratePlan}
                            disabled={isPlanning}
                          >
                            {isPlanning ? (
                              <><span className="scan-spinner">◌</span> Generating Proposal…</>
                            ) : (
                              <><span>⚡</span> {organizationPlan ? 'Re-Generate Proposal' : 'Generate Organization Plan'}</>
                            )}
                          </button>
                        </div>

                        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                          <input
                            id="plan-objective-input"
                            type="text"
                            className="text-input"
                            placeholder="Optional directive (e.g. Organize by category and project)"
                            value={organizationObjective}
                            onChange={(e) => setOrganizationObjective(e.target.value)}
                            style={{ flex: 1 }}
                          />
                        </div>

                        {organizationError && (
                          <div className="alert-box alert-danger" style={{ marginBottom: '1rem' }}>
                            <span className="alert-icon">✕</span><div>{organizationError}</div>
                          </div>
                        )}

                        {organizationPlan && (
                          <div className="plan-card">
                            <div className="plan-readonly-notice">
                              <span>ℹ</span>
                              <span><strong>Read-Only Proposal:</strong> No files or folders have been moved or modified on disk. All operations are planned proposals for review.</span>
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem', fontSize: '0.85rem' }}>
                              <div>
                                <span className="text-muted">Plan ID: </span>
                                <code style={{ color: '#a5b4fc' }}>{organizationPlan.plan_id.slice(0, 8)}...</code>
                              </div>
                              <div>
                                <span className="text-muted">Planned at: </span>
                                <span>{formatDate(organizationPlan.created_at)}</span>
                              </div>
                              <div>
                                <span className="text-muted">Directive: </span>
                                <em>{organizationPlan.objective || 'Default Categorization'}</em>
                              </div>
                            </div>

                            <div className="plan-summary-grid">
                              <div className="stat-box">
                                <div className="stat-value text-primary">{organizationPlan.operations.length}</div>
                                <div className="stat-label">Planned Ops</div>
                              </div>
                              {Object.entries(organizationPlan.summary).map(([opType, count]) => (
                                <div key={opType} className="stat-box">
                                  <div className="stat-value text-accent">{count}</div>
                                  <div className="stat-label">Proposed {opType}</div>
                                </div>
                              ))}
                              {approvalStatus && (
                                <>
                                  <div className="stat-box" style={{ borderColor: 'var(--success-color)' }}>
                                    <div className="stat-value text-success">{approvalStatus.approved_count}</div>
                                    <div className="stat-label">Approved</div>
                                  </div>
                                  <div className="stat-box" style={{ borderColor: 'var(--danger-color)' }}>
                                    <div className="stat-value text-danger">{approvalStatus.rejected_count}</div>
                                    <div className="stat-label">Rejected</div>
                                  </div>
                                  <div className="stat-box">
                                    <div className="stat-value" style={{ color: 'var(--text-dim)' }}>{approvalStatus.pending_count}</div>
                                    <div className="stat-label">Pending</div>
                                  </div>
                                </>
                              )}
                              {organizationPlan.warnings.length > 0 && (
                                <div className="stat-box">
                                  <div className="stat-value" style={{ color: 'var(--status-warning)' }}>{organizationPlan.warnings.length}</div>
                                  <div className="stat-label">Warnings</div>
                                </div>
                              )}
                              {organizationPlan.protected_conflicts.length > 0 && (
                                <div className="stat-box">
                                  <div className="stat-value text-danger">{organizationPlan.protected_conflicts.length}</div>
                                  <div className="stat-label">Blocked (Protected)</div>
                                </div>
                              )}
                            </div>

                            {/* Phase 11: Approval safety notice */}
                            <div style={{
                              background: 'rgba(16, 185, 129, 0.08)',
                              border: '1px solid rgba(16, 185, 129, 0.3)',
                              borderRadius: '8px',
                              padding: '0.75rem 1rem',
                              marginTop: '1rem',
                              fontSize: '0.875rem',
                              display: 'flex',
                              alignItems: 'flex-start',
                              gap: '0.5rem',
                            }}>
                              <span style={{ fontSize: '1.1rem' }}>🔒</span>
                              <div>
                                <strong style={{ color: '#6ee7b7' }}>Approved for later execution. No files have been changed.</strong>
                                <div style={{ color: 'var(--text-muted)', marginTop: '2px' }}>
                                  Use the Approve / Reject controls below to record your decisions.
                                  Decisions are stored for audit purposes only and do not execute any operations.
                                </div>
                              </div>
                            </div>

                            {approvalError && (
                              <div className="alert-box alert-danger" style={{ marginTop: '0.75rem' }}>
                                <span className="alert-icon">✕</span><div>{approvalError}</div>
                              </div>
                            )}

                            {organizationPlan.warnings.length > 0 && (
                              <div className="plan-warnings-box">
                                <strong>⚠️ Review Warnings:</strong>
                                <ul style={{ marginTop: '0.25rem', paddingLeft: '1.25rem' }}>
                                  {organizationPlan.warnings.map((w, idx) => (
                                    <li key={idx}>{w}</li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {organizationPlan.protected_conflicts.length > 0 && (
                              <div className="plan-protected-box">
                                <strong>🛡️ Blocked Security / Protected Path Operations:</strong>
                                <ul style={{ marginTop: '0.25rem', paddingLeft: '1.25rem' }}>
                                  {organizationPlan.protected_conflicts.map((c, idx) => (
                                    <li key={idx}>{c}</li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {/* Phase 12: Execution Controls */}
                            {approvalStatus && approvalStatus.approved_count > 0 && (
                              <div style={{
                                background: 'rgba(59, 130, 246, 0.08)',
                                border: '1px solid rgba(59, 130, 246, 0.3)',
                                borderRadius: '8px',
                                padding: '1rem',
                                marginTop: '1rem',
                              }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
                                  <div>
                                    <strong style={{ color: '#93c5fd' }}>Execution Engine</strong>
                                    <div style={{ color: 'var(--text-muted)', marginTop: '4px', fontSize: '0.85rem' }}>
                                      Ready to safely apply {approvalStatus.approved_count} approved operation(s) to the filesystem.
                                    </div>
                                  </div>
                                  <button
                                    className="btn btn-primary"
                                    onClick={handleExecutePlan}
                                    disabled={isExecuting || (executionStatus !== null && executionStatus.pending_count === 0 && executionStatus.in_progress_count === 0)}
                                  >
                                    {isExecuting ? 'Executing...' : 'Execute Approved Actions'}
                                  </button>
                                </div>
                                {executionError && (
                                  <div className="alert-box alert-danger" style={{ marginTop: '0.75rem' }}>
                                    <span className="alert-icon">✕</span><div>{executionError}</div>
                                  </div>
                                )}
                                {executionStatus && (
                                  <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem', fontSize: '0.85rem', flexWrap: 'wrap' }}>
                                    <span style={{ color: '#6ee7b7' }}>✓ {executionStatus.completed_count} Completed</span>
                                    {executionStatus.failed_count > 0 && <span style={{ color: '#fca5a5' }}>✕ {executionStatus.failed_count} Failed</span>}
                                    {executionStatus.skipped_count > 0 && <span style={{ color: '#fcd34d' }}>⚠ {executionStatus.skipped_count} Skipped</span>}
                                    {executionStatus.in_progress_count > 0 && <span style={{ color: '#93c5fd' }}>◌ {executionStatus.in_progress_count} In Progress</span>}
                                    {executionStatus.pending_count > 0 && <span style={{ color: 'var(--text-dim)' }}>○ {executionStatus.pending_count} Pending</span>}
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Phase 13: Verification Controls */}
                            {executionStatus && executionStatus.completed_count > 0 && (
                              <div style={{
                                background: 'rgba(139, 92, 246, 0.08)',
                                border: '1px solid rgba(139, 92, 246, 0.3)',
                                borderRadius: '8px',
                                padding: '1rem',
                                marginTop: '1rem',
                              }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
                                  <div>
                                    <strong style={{ color: '#c4b5fd' }}>Verification</strong>
                                    <div style={{ color: 'var(--text-muted)', marginTop: '4px', fontSize: '0.85rem' }}>
                                      Verify filesystem outcomes of {executionStatus.completed_count} completed operations. Does not mutate the filesystem.
                                    </div>
                                  </div>
                                  <button
                                    className="btn btn-primary"
                                    onClick={handleVerifyPlan}
                                    disabled={isVerifying}
                                    style={{ background: '#8b5cf6', borderColor: '#8b5cf6' }}
                                  >
                                    {isVerifying ? 'Verifying...' : 'Verify Outcomes'}
                                  </button>
                                </div>
                                {verificationError && (
                                  <div className="alert-box alert-danger" style={{ marginTop: '0.75rem' }}>
                                    <span className="alert-icon">✕</span><div>{verificationError}</div>
                                  </div>
                                )}
                                {verificationStatus && (
                                  <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem', fontSize: '0.85rem', flexWrap: 'wrap' }}>
                                    <span style={{ color: '#6ee7b7' }}>✓ {verificationStatus.verified_count} Verified</span>
                                    {verificationStatus.failed_count > 0 && <span style={{ color: '#fca5a5' }}>✕ {verificationStatus.failed_count} Failed</span>}
                                    {verificationStatus.inconclusive_count > 0 && <span style={{ color: '#fcd34d' }}>⚠ {verificationStatus.inconclusive_count} Inconclusive</span>}
                                    {verificationStatus.unverified_count > 0 && <span style={{ color: 'var(--text-dim)' }}>○ {verificationStatus.unverified_count} Unverified</span>}
                                  </div>
                                )}
                              </div>
                            )}

                            <h4 style={{ margin: '1rem 0 0.5rem 0', fontSize: '0.95rem' }}>
                              Proposed Operations ({organizationPlan.operations.length})
                            </h4>

                            {organizationPlan.operations.length === 0 ? (
                              <p className="text-small text-muted">All files are already optimally placed according to current rules. No moves planned.</p>
                            ) : (
                              <div style={{ maxHeight: '500px', overflowY: 'auto' }}>
                                {(approvalStatus ? approvalStatus.operations : organizationPlan.operations.map((op, idx) => ({
                                  operation_id: idx,
                                  plan_id: organizationPlan.plan_id,
                                  type: op.type,
                                  source: op.source,
                                  destination: op.destination,
                                  reason: op.reason,
                                  confidence: op.confidence,
                                  approval_decision: 'PENDING' as const,
                                  comment: null,
                                  decided_at: null,
                                  is_eligible: true,
                                  ineligibility_reason: null,
                                } as OperationWithApproval))).map((op, idx) => {
                                  const approvalDecision = op.approval_decision;
                                  const isApproved = approvalDecision === 'APPROVED';
                                  const isRejected = approvalDecision === 'REJECTED';
                                  const isDeciding = decidingOp === op.operation_id;

                                  return (
                                    <div
                                      key={op.operation_id !== undefined ? op.operation_id : idx}
                                      className="plan-op-item"
                                      style={{
                                        borderLeft: isApproved
                                          ? '3px solid var(--success-color)'
                                          : isRejected
                                          ? '3px solid var(--danger-color)'
                                          : '3px solid transparent',
                                        opacity: isRejected ? 0.7 : 1,
                                      }}
                                    >
                                      <div className="plan-op-header">
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                          <span className={`op-badge ${
                                            op.type === 'MOVE' ? 'op-badge-move' :
                                            op.type === 'RENAME' ? 'op-badge-rename' :
                                            'op-badge-folder'
                                          }`}>
                                            Proposed {op.type}
                                          </span>
                                          <span className="text-small" style={{ color: 'var(--text-muted)' }}>
                                            Planned Step #{idx + 1}
                                          </span>
                                          {/* Phase 11: approval status badge */}
                                          {isApproved && (
                                            <span style={{
                                              fontSize: '0.7rem', fontWeight: 700,
                                              background: 'rgba(16,185,129,0.2)',
                                              color: '#6ee7b7',
                                              border: '1px solid rgba(16,185,129,0.4)',
                                              borderRadius: '4px', padding: '1px 7px',
                                            }}>✓ APPROVED</span>
                                          )}
                                          {isRejected && (
                                            <span style={{
                                              fontSize: '0.7rem', fontWeight: 700,
                                              background: 'rgba(239,68,68,0.15)',
                                              color: '#fca5a5',
                                              border: '1px solid rgba(239,68,68,0.3)',
                                              borderRadius: '4px', padding: '1px 7px',
                                            }}>✕ REJECTED</span>
                                          )}
                                          {!op.is_eligible && (
                                            <span style={{
                                              fontSize: '0.7rem',
                                              background: 'rgba(234,179,8,0.15)',
                                              color: '#fde047',
                                              border: '1px solid rgba(234,179,8,0.3)',
                                              borderRadius: '4px', padding: '1px 7px',
                                            }}>⚠ Ineligible</span>
                                          )}
                                        </div>
                                        <span className={`confidence-tag ${
                                          op.confidence === 'HIGH' ? 'confidence-high' :
                                          op.confidence === 'MEDIUM' ? 'confidence-medium' :
                                          'confidence-low'
                                        }`}>
                                          {op.confidence} confidence
                                        </span>
                                      </div>

                                      <div className="plan-path-row">
                                        <span style={{ color: 'var(--text-dim)' }}>Source:</span>
                                        <span>{op.source}</span>
                                        <span className="plan-path-arrow">➔</span>
                                        <span style={{ color: 'var(--text-dim)' }}>Target:</span>
                                        <span style={{ color: '#93c5fd' }}>{op.destination}</span>
                                      </div>
                                      <div className="plan-reason">
                                        <span style={{ opacity: 0.7 }}>Reason: </span>
                                        <span>{op.reason}</span>
                                      </div>

                                      {/* Ineligibility warning */}
                                      {!op.is_eligible && op.ineligibility_reason && (
                                        <div style={{
                                          marginTop: '0.5rem', padding: '0.4rem 0.75rem',
                                          background: 'rgba(234,179,8,0.08)',
                                          border: '1px solid rgba(234,179,8,0.25)',
                                          borderRadius: '4px',
                                          fontSize: '0.78rem',
                                          color: '#fde047',
                                        }}>
                                          ⚠ {op.ineligibility_reason}
                                        </div>
                                      )}

                                      {/* Phase 11: Approve / Reject controls */}
                                      {approvalStatus && (
                                        <div style={{
                                          display: 'flex', gap: '0.5rem', marginTop: '0.75rem',
                                          paddingTop: '0.5rem',
                                          borderTop: '1px solid var(--border-subtle)',
                                          alignItems: 'center',
                                        }}>
                                          <button
                                            id={`approve-op-${op.operation_id}`}
                                            className="btn btn-sm"
                                            style={{
                                              fontSize: '0.78rem', padding: '3px 12px',
                                              background: isApproved ? 'rgba(16,185,129,0.3)' : 'rgba(16,185,129,0.1)',
                                              color: '#6ee7b7',
                                              border: `1px solid ${isApproved ? 'rgba(16,185,129,0.6)' : 'rgba(16,185,129,0.3)'}`,
                                              borderRadius: '5px',
                                              cursor: (!op.is_eligible || isDeciding) ? 'not-allowed' : 'pointer',
                                              opacity: (!op.is_eligible || isDeciding) ? 0.5 : 1,
                                              fontWeight: isApproved ? 700 : 400,
                                            }}
                                            disabled={!op.is_eligible || isDeciding}
                                            title={!op.is_eligible ? (op.ineligibility_reason || 'Ineligible') : 'Approve this operation'}
                                            onClick={() => handleDecideOperation(op.plan_id, op.operation_id, 'APPROVED')}
                                          >
                                            {isDeciding ? '…' : '✓ Approve'}
                                          </button>
                                          <button
                                            id={`reject-op-${op.operation_id}`}
                                            className="btn btn-sm"
                                            style={{
                                              fontSize: '0.78rem', padding: '3px 12px',
                                              background: isRejected ? 'rgba(239,68,68,0.3)' : 'rgba(239,68,68,0.08)',
                                              color: '#fca5a5',
                                              border: `1px solid ${isRejected ? 'rgba(239,68,68,0.5)' : 'rgba(239,68,68,0.25)'}`,
                                              borderRadius: '5px',
                                              cursor: (!op.is_eligible || isDeciding) ? 'not-allowed' : 'pointer',
                                              opacity: (!op.is_eligible || isDeciding) ? 0.5 : 1,
                                              fontWeight: isRejected ? 700 : 400,
                                            }}
                                            disabled={!op.is_eligible || isDeciding}
                                            title={!op.is_eligible ? (op.ineligibility_reason || 'Ineligible') : 'Reject this operation'}
                                            onClick={() => handleDecideOperation(op.plan_id, op.operation_id, 'REJECTED')}
                                          >
                                            {isDeciding ? '…' : '✕ Reject'}
                                          </button>
                                          {op.comment && (
                                            <span className="text-small" style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                                              Note: {op.comment}
                                            </span>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Skips */}
                    {scanResult.skip_count > 0 && (
                      <div className="skip-notice">
                        <span className="skip-icon">⚠</span>
                        <span>
                          {scanResult.skip_count} {scanResult.skip_count === 1 ? 'entry was' : 'entries were'} skipped
                          (symlinks, permission errors, or race conditions). The scan continued safely.
                        </span>
                      </div>
                    )}

                    {scanResult.file_count === 0 && scanResult.directory_count === 0 && (
                      <div className="scan-empty">
                        <span className="scan-empty-icon">🗂️</span>
                        <p>The authorized root is empty — no files or folders were found.</p>
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        </section>

        {/* System & API Status Grid */}
        <div className="grid">
          {/* Frontend Component Status */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">Frontend Service</h2>
              <span className="badge badge-success">● Running</span>
            </div>
            <div className="card-body">
              <div className="info-row">
                <span className="info-label">Framework</span>
                <span className="info-value">React 19 + Vite (TypeScript)</span>
              </div>
              <div className="info-row">
                <span className="info-label">Execution Target</span>
                <span className="info-value">Local Browser Sandbox</span>
              </div>
              <div className="info-row">
                <span className="info-label">Direct Filesystem Access</span>
                <span className="info-value text-muted">Blocked (Sandbox Guard)</span>
              </div>
            </div>
            <div className="card-footer">
              <span className="text-small text-muted">Vite development server active</span>
            </div>
          </div>

          {/* Backend Health Check Card */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">Backend API</h2>
              {getStatusBadge()}
            </div>
            <div className="card-body">
              <div className="info-row">
                <span className="info-label">Health Endpoint</span>
                <code className="code-snippet">GET /api/health</code>
              </div>
              <div className="info-row">
                <span className="info-label">Folder Endpoints</span>
                <code className="code-snippet">GET/POST /api/folder</code>
              </div>
              <div className="info-row">
                <span className="info-label">Scan Endpoint</span>
                <code className="code-snippet">POST /api/scan</code>
              </div>
              <div className="info-row">
                <span className="info-label">Latency</span>
                <span className="info-value">
                  {healthResult.latencyMs !== undefined ? `${healthResult.latencyMs} ms` : '—'}
                </span>
              </div>
              <div className="info-row">
                <span className="info-label">Last Checked</span>
                <span className="info-value">{healthResult.checkedAt}</span>
              </div>

              {healthResult.status === 'connected' && healthResult.data && (
                <div className="payload-box">
                  <div className="payload-title">Health Payload:</div>
                  <pre className="payload-code">
                    {JSON.stringify(healthResult.data, null, 2)}
                  </pre>
                </div>
              )}

              {healthResult.status === 'unavailable' && (
                <div className="error-box">
                  <div className="error-title">Connection Failure:</div>
                  <p className="error-message">{healthResult.error}</p>
                  <div className="troubleshooting">
                    <strong>To start the backend service:</strong>
                    <code>cd backend; uvicorn app.main:app --reload --port 8000</code>
                  </div>
                </div>
              )}
            </div>

            <div className="card-footer card-actions">
              <button
                id="refresh-health-btn"
                className="btn btn-primary"
                onClick={checkHealthStatus}
                disabled={isRefreshingHealth}
              >
                {isRefreshingHealth ? 'Checking...' : 'Refresh Status'}
              </button>
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={autoPollHealth}
                  onChange={(e) => setAutoPollHealth(e.target.checked)}
                />
                Auto-refresh (5s)
              </label>
            </div>
          </div>
        </div>

        {/* Phase Status Checklist */}
        <section className="checklist-card">
          <h3 className="checklist-title">Phase 2 Security &amp; Scanner Status</h3>
          <div className="checklist-grid">
            <div className="check-item passed">
              <span className="check-icon">✓</span>
              <span>Backend Path Normalization &amp; Canonicalization</span>
            </div>
            <div className="check-item passed">
              <span className="check-icon">✓</span>
              <span>Root Existence, Type &amp; Readability Validation</span>
            </div>
            <div className={`check-item ${folderStatus.is_configured ? 'passed' : 'pending'}`}>
              <span className="check-icon">{folderStatus.is_configured ? '✓' : '○'}</span>
              <span>Authorized Root Established via API</span>
            </div>
            <div className="check-item passed">
              <span className="check-icon">✓</span>
              <span>Path Traversal (..) &amp; Escape Prevention Service</span>
            </div>
            <div className="check-item passed">
              <span className="check-icon">✓</span>
              <span>Symlink / Junction Traversal Blocked</span>
            </div>
            <div className={`check-item ${scanResult?.status === 'completed' ? 'passed' : 'pending'}`}>
              <span className="check-icon">{scanResult?.status === 'completed' ? '✓' : '○'}</span>
              <span>Recursive Filesystem Scanner Active</span>
            </div>
            <div className="check-item passed">
              <span className="check-icon">✓</span>
              <span>Read-Only Scanner (No Mutations)</span>
            </div>
            <div className="check-item passed">
              <span className="check-icon">✓</span>
              <span>Structured Error &amp; Skip Handling</span>
            </div>
          </div>
        </section>
      </main>

      <footer className="footer">
        <p>SmartSort • Phase 5 Document Extraction • Local-first execution architecture</p>
      </footer>
    </div>
  );
}
