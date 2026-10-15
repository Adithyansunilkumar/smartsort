import { useEffect, useState, useCallback, useMemo, type FormEvent } from 'react';
import {
  fetchCurrentFolder,
  selectFolder,
  clearFolder,
  startScan,
  analyzeFiles,
  generateOrganizationPlan,
  type FolderStatusResponse,
  type ScanResult,
  type AnalysisSummary,
  type OrganizationPlan,
  getPlanApprovals,
  decideOperation,
  bulkApproveOperations,
  bulkRejectOperations,
  type PlanApprovalStatus,
  executePlan,
  getPlanExecutions,
  getOutputFolder,
  type PlanExecutionStatus,
  type OutputFolderInfo,
  verifyPlan,
  type PlanVerificationStatus,
} from './services/api';

// ─── App Workflow Steps ───────────────────────────────────────────────────────
type WorkflowStep = 'folder' | 'scan' | 'analyze' | 'plan' | 'review' | 'done';

export default function App() {
  // ── Core workflow state ────────────────────────────────────────────────────
  const [step, setStep] = useState<WorkflowStep>('folder');
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);

  // ── Folder state ───────────────────────────────────────────────────────────
  const [folderStatus, setFolderStatus] = useState<FolderStatusResponse>({
    is_configured: false,
    authorized_root: null,
    message: '',
  });
  const [candidatePath, setCandidatePath] = useState<string>('');
  const [isSubmittingFolder, setIsSubmittingFolder] = useState(false);
  const [folderError, setFolderError] = useState<string | null>(null);

  // ── Scan state ─────────────────────────────────────────────────────────────
  const [isScanning, setIsScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);

  // ── Analysis state ─────────────────────────────────────────────────────────
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<AnalysisSummary | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysisCategoryFilter, setAnalysisCategoryFilter] = useState<string>('all');
  const [analysisSearchQuery, setAnalysisSearchQuery] = useState<string>('');

  // ── Organization plan state ────────────────────────────────────────────────
  const [isPlanning, setIsPlanning] = useState(false);
  const [organizationPlan, setOrganizationPlan] = useState<OrganizationPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  // ── Approval state ─────────────────────────────────────────────────────────
  const [approvalStatus, setApprovalStatus] = useState<PlanApprovalStatus | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [decidingOp, setDecidingOp] = useState<number | null>(null);
  const [isBulkApproving, setIsBulkApproving] = useState(false);
  const [isBulkRejecting, setIsBulkRejecting] = useState(false);
  const [showRejectAllConfirm, setShowRejectAllConfirm] = useState(false);
  const [showOpsDetail, setShowOpsDetail] = useState(true);
  const [approvalFilter, setApprovalFilter] = useState<'all' | 'pending' | 'approved' | 'rejected' | 'needs_review'>('all');
  const [approvalSearchQuery, setApprovalSearchQuery] = useState<string>('');

  // ── Execution state ────────────────────────────────────────────────────────
  const [isExecuting, setIsExecuting] = useState(false);
  const [executionStatus, setExecutionStatus] = useState<PlanExecutionStatus | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [outputFolderInfo, setOutputFolderInfo] = useState<OutputFolderInfo | null>(null);

  // ── Verification state ─────────────────────────────────────────────────────
  const [isVerifying, setIsVerifying] = useState(false);
  const [verificationStatus, setVerificationStatus] = useState<PlanVerificationStatus | null>(null);

  // ── Backend connectivity check ─────────────────────────────────────────────
  useEffect(() => {
    fetch('http://127.0.0.1:8000/api/health')
      .then(r => { setBackendOnline(r.ok); })
      .catch(() => setBackendOnline(false));
  }, []);

  // ── Load folder status on mount ────────────────────────────────────────────
  useEffect(() => {
    fetchCurrentFolder().then(res => {
      if (res.success && res.data && 'is_configured' in res.data) {
        const fs = res.data as FolderStatusResponse;
        setFolderStatus(fs);
        if (fs.is_configured) setStep('scan');
      }
    });
  }, []);

  // ── Execution polling ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!executionStatus || (executionStatus.in_progress_count === 0 && executionStatus.pending_count === 0)) return;
    const timer = setInterval(async () => {
      const res = await getPlanExecutions(executionStatus.plan_id);
      if (res.success && res.data) setExecutionStatus(res.data);
    }, 2000);
    return () => clearInterval(timer);
  }, [executionStatus]);

  // ── Helpers ────────────────────────────────────────────────────────────────
  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024, sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };

  const getFileIcon = (ext: string): string => {
    const e = ext.toLowerCase();
    if (['.pdf'].includes(e)) return '📕';
    if (['.doc', '.docx', '.txt', '.rtf', '.odt', '.md'].includes(e)) return '📝';
    if (['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'].includes(e)) return '🖼️';
    if (['.mp3', '.wav', '.flac', '.aac', '.ogg'].includes(e)) return '🎵';
    if (['.mp4', '.mov', '.avi', '.mkv', '.webm'].includes(e)) return '🎬';
    if (['.zip', '.rar', '.7z', '.tar', '.gz'].includes(e)) return '📦';
    if (['.js', '.ts', '.py', '.java', '.cpp', '.c', '.go', '.rs', '.html', '.css', '.json'].includes(e)) return '💻';
    if (['.xls', '.xlsx', '.csv', '.tsv'].includes(e)) return '📊';
    if (['.ppt', '.pptx'].includes(e)) return '📋';
    return '📄';
  };

  const opTypeLabel = (type: string) => {
    if (type === 'MOVE' || type === 'COPY_FILE') return 'Copy / Organize';
    if (type === 'RENAME') return 'Rename';
    if (type === 'CREATE_FOLDER') return 'Create folder';
    return type;
  };

  // ── Step Navigation ────────────────────────────────────────────────────────
  const steps: { id: WorkflowStep; label: string }[] = [
    { id: 'folder', label: 'Choose Folder' },
    { id: 'scan', label: 'Scan Files' },
    { id: 'analyze', label: 'Analyze' },
    { id: 'plan', label: 'Plan' },
    { id: 'review', label: 'Review & Approve' },
    { id: 'done', label: 'Done' },
  ];

  const stepOrder: WorkflowStep[] = ['folder', 'scan', 'analyze', 'plan', 'review', 'done'];
  const currentStepIndex = stepOrder.indexOf(step);

  // ── Handlers ───────────────────────────────────────────────────────────────

  const handleSelectFolderSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedPath = candidatePath.trim();
    if (!trimmedPath) { setFolderError('Please enter a folder path.'); return; }
    setIsSubmittingFolder(true);
    setFolderError(null);
    const result = await selectFolder(trimmedPath);
    setIsSubmittingFolder(false);
    if (result.success && result.data && 'authorized_root' in result.data) {
      setFolderStatus({ is_configured: true, authorized_root: result.data.authorized_root, message: '' });
      setCandidatePath('');
      setScanResult(null);
      setAnalysisResult(null);
      setOrganizationPlan(null);
      setApprovalStatus(null);
      setExecutionStatus(null);
      setVerificationStatus(null);
      setStep('scan');
    } else {
      setFolderError(result.error || 'Could not authorize this folder. Check the path and try again.');
    }
  };

  const handleClearFolder = async () => {
    await clearFolder();
    setFolderStatus({ is_configured: false, authorized_root: null, message: '' });
    setScanResult(null);
    setAnalysisResult(null);
    setOrganizationPlan(null);
    setApprovalStatus(null);
    setExecutionStatus(null);
    setVerificationStatus(null);
    setStep('folder');
  };

  const handleStartScan = async () => {
    setIsScanning(true);
    setScanError(null);
    setScanResult(null);
    setAnalysisResult(null);
    setOrganizationPlan(null);
    setApprovalStatus(null);
    setExecutionStatus(null);
    setVerificationStatus(null);
    const result = await startScan();
    setIsScanning(false);
    if (result.success && result.data) {
      setScanResult(result.data);
      // Stay on 'scan' step so user can review scan results and choose to analyze
      setStep('scan');
    } else {
      setScanError(result.error || 'Scan failed. Please try again.');
    }
  };

  const handleRunAnalysis = useCallback(async () => {
    if (!scanResult) return;
    setIsAnalyzing(true);
    setAnalysisError(null);
    const analysisRes = await analyzeFiles(scanResult.scan_id);
    setIsAnalyzing(false);
    if (analysisRes.success && analysisRes.data) {
      setAnalysisResult(analysisRes.data);
    } else {
      setAnalysisError(analysisRes.error || 'Analysis failed. Please check backend connection and retry.');
    }
  }, [scanResult]);

  const handleGoToAnalyze = useCallback(async () => {
    setStep('analyze');
    if (!analysisResult || analysisResult.scan_id !== scanResult?.scan_id) {
      handleRunAnalysis();
    }
  }, [analysisResult, scanResult, handleRunAnalysis]);

  const handleGeneratePlan = useCallback(async () => {
    if (!scanResult) return;
    setIsPlanning(true);
    setPlanError(null);
    setApprovalStatus(null);
    const planRes = await generateOrganizationPlan(scanResult.scan_id, undefined);
    setIsPlanning(false);
    if (planRes.success && planRes.data) {
      setOrganizationPlan(planRes.data.plan);
      // Auto-load approval status for review
      const planId = planRes.data.plan.plan_id;
      const approvalRes = await getPlanApprovals(planId);
      if (approvalRes.success && approvalRes.data) {
        setApprovalStatus(approvalRes.data);
      }
    } else {
      setPlanError(planRes.error || 'Could not generate organization plan. Please try again.');
    }
  }, [scanResult]);

  const handleGoToPlan = useCallback(async () => {
    setStep('plan');
    if (!organizationPlan || organizationPlan.scan_id !== scanResult?.scan_id) {
      handleGeneratePlan();
    }
  }, [organizationPlan, scanResult, handleGeneratePlan]);

  const handleGoToReview = useCallback(async () => {
    if (!organizationPlan) return;
    setStep('review');
    const planId = organizationPlan.plan_id;
    const approvalRes = await getPlanApprovals(planId);
    if (approvalRes.success && approvalRes.data) setApprovalStatus(approvalRes.data);
    const execRes = await getPlanExecutions(planId);
    if (execRes.success && execRes.data) setExecutionStatus(execRes.data);
  }, [organizationPlan]);

  const handleDecideOperation = useCallback(async (
    planId: string,
    operationId: number,
    decision: 'APPROVED' | 'REJECTED',
  ) => {
    setDecidingOp(operationId);
    setApprovalError(null);
    await decideOperation(planId, operationId, decision);
    setDecidingOp(null);
    const refreshed = await getPlanApprovals(planId);
    if (refreshed.success && refreshed.data) setApprovalStatus(refreshed.data);
  }, []);

  const handleApproveAll = useCallback(async () => {
    if (!organizationPlan || isBulkApproving) return;
    setIsBulkApproving(true);
    setApprovalError(null);
    const result = await bulkApproveOperations(organizationPlan.plan_id);
    setIsBulkApproving(false);
    if (result.success && result.data) {
      setApprovalStatus(result.data.plan_status);
    } else {
      setApprovalError(result.error || 'Bulk approval failed.');
    }
  }, [organizationPlan, isBulkApproving]);

  const handleRejectAll = useCallback(async () => {
    if (!organizationPlan || isBulkRejecting) return;
    setShowRejectAllConfirm(false);
    setIsBulkRejecting(true);
    setApprovalError(null);
    const result = await bulkRejectOperations(organizationPlan.plan_id);
    setIsBulkRejecting(false);
    if (result.success && result.data) {
      setApprovalStatus(result.data.plan_status);
    } else {
      setApprovalError(result.error || 'Bulk rejection failed.');
    }
  }, [organizationPlan, isBulkRejecting]);

  const handleExecutePlan = useCallback(async () => {
    if (!organizationPlan || isExecuting) return;
    setIsExecuting(true);
    setExecutionError(null);
    const result = await executePlan(organizationPlan.plan_id);
    if (result.success && result.data) {
      setExecutionStatus(result.data);
      // Fetch output folder info
      const outputRes = await getOutputFolder();
      if (outputRes.success && outputRes.data) setOutputFolderInfo(outputRes.data);
      setStep('done');
    } else {
      setExecutionError(result.error || 'Execution failed.');
    }
    setIsExecuting(false);
  }, [organizationPlan, isExecuting]);

  const handleVerify = useCallback(async () => {
    if (!organizationPlan || isVerifying) return;
    setIsVerifying(true);
    const result = await verifyPlan(organizationPlan.plan_id);
    if (result.success && result.data) setVerificationStatus(result.data);
    setIsVerifying(false);
  }, [organizationPlan, isVerifying]);

  // ── Derived Analysis Data ──────────────────────────────────────────────────
  const unclassifiedFiles = useMemo(() => {
    if (!analysisResult) return [];
    return analysisResult.documents.filter(d => d.category === 'unknown' || d.category === 'other');
  }, [analysisResult]);

  const filteredAnalysisFiles = useMemo(() => {
    if (!analysisResult) return [];
    return analysisResult.documents.filter(d => {
      const matchesCategory = analysisCategoryFilter === 'all' || d.category === analysisCategoryFilter;
      const matchesSearch = !analysisSearchQuery.trim() ||
        d.relative_path.toLowerCase().includes(analysisSearchQuery.toLowerCase());
      return matchesCategory && matchesSearch;
    });
  }, [analysisResult, analysisCategoryFilter, analysisSearchQuery]);

  // ── Derived Plan Data ──────────────────────────────────────────────────────
  const proposedFolders = useMemo(() => {
    if (!organizationPlan) return [];
    const folders = new Set<string>();
    for (const op of organizationPlan.operations) {
      if (op.type === 'CREATE_FOLDER') {
        folders.add(op.destination);
      } else if (op.destination) {
        const parts = op.destination.split('/');
        if (parts.length > 1) {
          folders.add(parts.slice(0, -1).join('/'));
        }
      }
    }
    return Array.from(folders).sort();
  }, [organizationPlan]);

  const pendingCount = approvalStatus?.pending_count ?? 0;
  const approvedCount = approvalStatus?.approved_count ?? 0;
  const rejectedCount = approvalStatus?.rejected_count ?? 0;
  const needsReviewCount = useMemo(() => {
    if (!approvalStatus) return 0;
    return approvalStatus.operations.filter(op => op.needs_review).length;
  }, [approvalStatus]);
  const totalOps = approvalStatus?.total_operations ?? organizationPlan?.operations.length ?? 0;

  const filteredApprovalOps = useMemo(() => {
    if (!approvalStatus) return [];
    return approvalStatus.operations.filter(op => {
      const isApproved = op.approval_decision === 'APPROVED';
      const isRejected = op.approval_decision === 'REJECTED';
      const isPending = !isApproved && !isRejected;

      if (approvalFilter === 'approved' && !isApproved) return false;
      if (approvalFilter === 'rejected' && !isRejected) return false;
      if (approvalFilter === 'pending' && !isPending) return false;
      if (approvalFilter === 'needs_review' && !op.needs_review) return false;

      if (approvalSearchQuery.trim()) {
        const q = approvalSearchQuery.toLowerCase();
        const sourceMatch = op.source.toLowerCase().includes(q);
        const destMatch = op.destination.toLowerCase().includes(q);
        const reasonMatch = op.reason ? op.reason.toLowerCase().includes(q) : false;
        const flagsMatch = op.flags ? op.flags.toLowerCase().includes(q) : false;
        if (!sourceMatch && !destMatch && !reasonMatch && !flagsMatch) return false;
      }

      return true;
    });
  }, [approvalStatus, approvalFilter, approvalSearchQuery]);

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="app-shell">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="app-header">
        <div className="header-brand">
          <div className="brand-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 3h6l2 3h10a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>
              <path d="M12 10v6M9 13l3 3 3-3"/>
            </svg>
          </div>
          <div>
            <h1 className="brand-name">SmartSort</h1>
            <p className="brand-tagline">Organize your files intelligently</p>
          </div>
        </div>
        {backendOnline === false && (
          <div className="backend-warning">
            <span>⚠</span>
            <span>Backend service is not responding. Start the backend to continue.</span>
          </div>
        )}
        {folderStatus.is_configured && (
          <div className="header-folder">
            <span className="folder-icon">📁</span>
            <span className="folder-path-short" title={folderStatus.authorized_root || ''}>
              {folderStatus.authorized_root?.split(/[/\\]/).pop()}
            </span>
            <button className="btn-text" onClick={handleClearFolder}>Change</button>
          </div>
        )}
      </header>

      {/* ── Progress Steps ──────────────────────────────────────────────────── */}
      <div className="progress-bar-wrap">
        <div className="progress-steps">
          {steps.map((s, idx) => {
            const isActive = s.id === step;
            const isDone = stepOrder.indexOf(s.id) < currentStepIndex;
            const canNavigate = isDone || isActive || (s.id === 'analyze' && !!scanResult) || (s.id === 'plan' && !!analysisResult);
            return (
              <div
                key={s.id}
                className={`progress-step ${isActive ? 'active' : ''} ${isDone ? 'done' : ''}`}
                style={{ cursor: canNavigate ? 'pointer' : 'default' }}
                onClick={() => {
                  if (canNavigate) {
                    if (s.id === 'analyze') handleGoToAnalyze();
                    else if (s.id === 'plan') handleGoToPlan();
                    else if (s.id === 'review' && organizationPlan) handleGoToReview();
                    else setStep(s.id);
                  }
                }}
              >
                <div className="step-dot">
                  {isDone ? '✓' : idx + 1}
                </div>
                <span className="step-label">{s.label}</span>
                {idx < steps.length - 1 && <div className={`step-connector ${isDone ? 'done' : ''}`} />}
              </div>
            );
          })}
        </div>
      </div>

      <main className="app-main">

        {/* ══ STEP 1: Choose Folder ══════════════════════════════════════════ */}
        {step === 'folder' && (
          <section className="step-card">
            <div className="step-card-icon">📂</div>
            <h2 className="step-card-title">Choose a Folder to Organize</h2>
            <p className="step-card-desc">
              Enter the full folder path on your computer. SmartSort will scan your files, classify them by category,
              create an organization plan, and let you review everything before making any changes.
            </p>

            <form onSubmit={handleSelectFolderSubmit} className="folder-form">
              <div className="input-row">
                <input
                  id="folder-path-input"
                  type="text"
                  className="text-input"
                  placeholder="Paste full folder path — e.g. C:\Users\Username\Documents"
                  value={candidatePath}
                  onChange={e => setCandidatePath(e.target.value)}
                  disabled={isSubmittingFolder}
                  autoFocus
                />
                <button
                  id="submit-folder-btn"
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSubmittingFolder || !candidatePath.trim()}
                >
                  {isSubmittingFolder ? 'Checking…' : 'Use This Folder'}
                </button>
              </div>
            </form>

            {folderError && (
              <div className="alert alert-error" style={{ marginTop: '1.25rem' }}>
                <span>⚠</span>
                <div>{folderError}</div>
              </div>
            )}

            <div className="info-callout" style={{ marginTop: '1.5rem' }}>
              <strong>🔒 Safe and Non-Destructive.</strong> SmartSort operates in read-only mode initially.
              Organized copies are placed into a separate <code>SmartSort_Organized</code> folder — your
              originals are never deleted or modified.
            </div>
          </section>
        )}

        {/* ══ STEP 2: Scan ══════════════════════════════════════════════════ */}
        {step === 'scan' && (
          <section className="step-card">
            <div className="step-card-header">
              <div>
                <div className="step-card-icon">🔍</div>
                <h2 className="step-card-title">Scan Your Files</h2>
                <p className="step-card-desc">
                  Discover all files and folders in <strong>{folderStatus.authorized_root?.split(/[/\\]/).pop()}</strong>.
                  Nothing is modified during scanning.
                </p>
              </div>
            </div>

            {!scanResult && !isScanning && (
              <button
                id="start-scan-btn"
                className="btn btn-primary btn-large"
                onClick={handleStartScan}
              >
                <span>🔍</span> Start Scan
              </button>
            )}

            {isScanning && (
              <div className="loading-state">
                <div className="spinner"></div>
                <span>Scanning files in directory…</span>
              </div>
            )}

            {scanError && (
              <div className="alert alert-error">
                <span>⚠</span>
                <div>{scanError}
                  <button className="btn-link" onClick={handleStartScan} style={{ marginLeft: '0.5rem' }}>Try again</button>
                </div>
              </div>
            )}

            {scanResult && !isScanning && (
              <>
                <div className="stats-row">
                  <div className="stat-card">
                    <div className="stat-number">{scanResult.file_count.toLocaleString()}</div>
                    <div className="stat-label">Files found</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-number">{scanResult.directory_count.toLocaleString()}</div>
                    <div className="stat-label">Subfolders</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-number">{formatBytes(scanResult.files.reduce((s, f) => s + f.size_bytes, 0))}</div>
                    <div className="stat-label">Total size</div>
                  </div>
                </div>

                {scanResult.skip_count > 0 && (
                  <div className="info-callout" style={{ textAlign: 'left', marginBottom: '1.25rem' }}>
                    <span>⚠</span> {scanResult.skip_count} inaccessible entry/entries skipped safely.
                  </div>
                )}

                {scanResult.file_count === 0 ? (
                  <div className="empty-state">
                    <span>🗂️</span>
                    <p>No files were found in this folder. Choose a different folder to continue.</p>
                    <div className="action-row" style={{ marginTop: '1rem' }}>
                      <button className="btn btn-secondary" onClick={handleClearFolder}>
                        Choose Different Folder
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="action-row">
                    <button
                      id="proceed-to-analyze-btn"
                      className="btn btn-primary btn-large"
                      onClick={handleGoToAnalyze}
                    >
                      <span>📊</span> Proceed to Analysis →
                    </button>
                    <button className="btn btn-secondary" onClick={handleStartScan}>
                      Re-Scan
                    </button>
                    <button className="btn btn-secondary" onClick={handleClearFolder}>
                      Change Folder
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
        )}

        {/* ══ STEP 3: Analyze ═══════════════════════════════════════════════ */}
        {step === 'analyze' && (
          <section className="analyze-section">
            <div className="review-header">
              <div>
                <h2 className="section-title">File Analysis &amp; Classification</h2>
                <p className="section-desc">
                  Deterministic classification of your scanned files by category, file type, recency, and duplication status.
                </p>
              </div>
              <div className="action-buttons-top">
                <button
                  className="btn btn-secondary"
                  onClick={handleRunAnalysis}
                  disabled={isAnalyzing}
                  title="Re-run analysis on the scanned files"
                >
                  {isAnalyzing ? <><span className="spinner-sm"></span> Analyzing…</> : <><span>🔄</span> Re-Analyze</>}
                </button>
                {analysisResult && analysisResult.total_analyzed > 0 && (
                  <button
                    id="top-generate-plan-btn"
                    className="btn btn-primary"
                    onClick={handleGoToPlan}
                    disabled={isPlanning}
                    title="Generate organization plan from current analysis"
                  >
                    <span>📋</span> Generate Plan →
                  </button>
                )}
              </div>
            </div>

            {isAnalyzing && (
              <div className="loading-state card" style={{ padding: '3rem', textAlign: 'center' }}>
                <div className="spinner"></div>
                <h3 style={{ marginTop: '1rem', marginBottom: '0.5rem' }}>Analyzing Files…</h3>
                <p className="text-muted" style={{ fontSize: '0.85rem' }}>
                  Extracting file types, detecting duplicate files, and categorizing documents…
                </p>
              </div>
            )}

            {analysisError && !isAnalyzing && (
              <div className="alert alert-error">
                <span>⚠</span>
                <div>
                  <strong>Analysis Error:</strong> {analysisError}
                  <div style={{ marginTop: '0.5rem' }}>
                    <button className="btn btn-secondary" onClick={handleRunAnalysis} style={{ marginRight: '0.5rem' }}>
                      Retry Analysis
                    </button>
                    <button className="btn btn-secondary" onClick={() => setStep('scan')}>
                      ← Back to Scan
                    </button>
                  </div>
                </div>
              </div>
            )}

            {!isAnalyzing && !analysisError && !analysisResult && (
              <div className="step-card">
                <div className="step-card-icon">📊</div>
                <h3 className="step-card-title">Ready to Analyze</h3>
                <p className="step-card-desc">Click below to analyze the {scanResult?.file_count || 0} scanned files.</p>
                <button className="btn btn-primary btn-large" onClick={handleRunAnalysis}>
                  <span>⚡</span> Run File Analysis
                </button>
              </div>
            )}

            {!isAnalyzing && analysisResult && (
              <>
                {/* Empty State */}
                {analysisResult.total_analyzed === 0 ? (
                  <div className="step-card">
                    <div className="step-card-icon">📭</div>
                    <h3 className="step-card-title">No Files to Analyze</h3>
                    <p className="step-card-desc">The current scan found no files to analyze.</p>
                    <div className="action-row">
                      <button className="btn btn-secondary" onClick={() => setStep('scan')}>
                        ← Back to Scan
                      </button>
                      <button className="btn btn-secondary" onClick={handleClearFolder}>
                        Change Folder
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {/* Stats Overview */}
                    <div className="stats-row">
                      <div className="stat-card">
                        <div className="stat-number">{analysisResult.total_analyzed.toLocaleString()}</div>
                        <div className="stat-label">Files Analyzed</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">{formatBytes(analysisResult.total_size_bytes)}</div>
                        <div className="stat-label">Total Size</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">{Object.keys(analysisResult.categories).length}</div>
                        <div className="stat-label">Categories</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">{analysisResult.duplicates_found}</div>
                        <div className="stat-label">Duplicates Flagged</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">{analysisResult.temp_or_copy_files}</div>
                        <div className="stat-label">Copies / Temp Files</div>
                      </div>
                    </div>

                    {/* Recommendation Callout */}
                    <div className="info-callout" style={{ margin: '0' }}>
                      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                        <span style={{ fontSize: '1.4rem' }}>💡</span>
                        <div>
                          <strong>Organization Recommendation:</strong> SmartSort identified{' '}
                          <strong>{Object.keys(analysisResult.categories).length} categories</strong> across{' '}
                          <strong>{analysisResult.total_analyzed} files</strong>. Generating an organization plan
                          will propose grouping files into categorized destination folders inside{' '}
                          <code>SmartSort_Organized/</code> and normalizing filename patterns (such as removing "Copy of").
                        </div>
                      </div>
                    </div>

                    {/* Category Distribution */}
                    <div className="categories-card">
                      <h3 className="card-title-sm">Assigned Categories &amp; File Distribution</h3>
                      <div className="category-chips">
                        <div
                          className={`category-chip ${analysisCategoryFilter === 'all' ? 'active-filter' : ''}`}
                          onClick={() => setAnalysisCategoryFilter('all')}
                          style={{ cursor: 'pointer' }}
                        >
                          <span className="cat-name">All Files</span>
                          <span className="cat-count">{analysisResult.total_analyzed}</span>
                        </div>
                        {Object.entries(analysisResult.categories)
                          .sort((a, b) => b[1] - a[1])
                          .map(([cat, count]) => (
                            <div
                              key={cat}
                              className={`category-chip ${analysisCategoryFilter === cat ? 'active-filter' : ''}`}
                              onClick={() => setAnalysisCategoryFilter(cat === analysisCategoryFilter ? 'all' : cat)}
                              style={{ cursor: 'pointer' }}
                            >
                              <span className="cat-name">{cat}</span>
                              <span className="cat-count">{count}</span>
                            </div>
                          ))}
                      </div>
                    </div>

                    {/* Unclassified / Unsupported Notice */}
                    {unclassifiedFiles.length > 0 ? (
                      <div className="alert alert-warning" style={{ margin: 0 }}>
                        <span>ℹ</span>
                        <div>
                          <strong>{unclassifiedFiles.length} file(s)</strong> could not be classified into specific
                          standard categories. These files will be safely organized into the root of{' '}
                          <code>SmartSort_Organized/</code> without category relocation.
                        </div>
                      </div>
                    ) : (
                      <div className="alert alert-success" style={{ margin: 0 }}>
                        <span>✓</span>
                        <div>All {analysisResult.total_analyzed} files were successfully classified into organizational categories.</div>
                      </div>
                    )}

                    {/* Analyzed Files Detail List */}
                    <div className="operations-section">
                      <div className="ops-header">
                        <h3 className="card-title-sm">
                          Analyzed Files ({filteredAnalysisFiles.length}
                          {filteredAnalysisFiles.length !== analysisResult.documents.length ? ` of ${analysisResult.documents.length}` : ''})
                        </h3>
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                          <input
                            type="text"
                            placeholder="Filter files by name…"
                            className="text-input"
                            style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem', width: '200px' }}
                            value={analysisSearchQuery}
                            onChange={e => setAnalysisSearchQuery(e.target.value)}
                          />
                        </div>
                      </div>

                      <div className="ops-list">
                        {filteredAnalysisFiles.map((doc) => {
                          const ext = doc.relative_path.includes('.') ? `.${doc.relative_path.split('.').pop()}` : '';
                          return (
                            <div key={doc.file_id} className="op-item">
                              <div className="op-icon">{getFileIcon(ext)}</div>
                              <div className="op-body">
                                <div className="op-paths">
                                  <span className="op-source" title={doc.relative_path}>
                                    {doc.relative_path}
                                  </span>
                                </div>
                                <div className="op-reason">
                                  <span>{formatBytes(doc.size_bytes)}</span> ·{' '}
                                  <span style={{ textTransform: 'capitalize' }}>{doc.category}</span> ·{' '}
                                  <span>{doc.age_bucket.replace('_', ' ')} ({doc.age_days}d)</span>
                                </div>
                              </div>
                              <div className="op-status" style={{ display: 'flex', gap: '0.35rem' }}>
                                <span className="category-chip" style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem' }}>
                                  {doc.category}
                                </span>
                                {doc.has_duplicates && (
                                  <span className="badge-warning" style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem' }}>
                                    Duplicate
                                  </span>
                                )}
                                {doc.is_copy && (
                                  <span className="badge-info" style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem' }}>
                                    Copy Pattern
                                  </span>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* Primary Action Row */}
                    <div className="action-row" style={{ marginTop: '1rem' }}>
                      <button
                        id="generate-plan-btn"
                        className="btn btn-primary btn-large"
                        onClick={handleGoToPlan}
                        disabled={isPlanning}
                      >
                        <span>📋</span> Generate Organization Plan →
                      </button>
                      <button className="btn btn-secondary" onClick={() => setStep('scan')}>
                        ← Back to Scan
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
          </section>
        )}

        {/* ══ STEP 4: Plan ══════════════════════════════════════════════════ */}
        {step === 'plan' && (
          <section className="analyze-section">
            <div className="review-header">
              <div>
                <h2 className="section-title">Organization Plan</h2>
                <p className="section-desc">
                  Review the proposed destination folder hierarchy and planned file operations based on your analysis results.
                </p>
              </div>
              <div className="action-buttons-top">
                <button
                  className="btn btn-secondary"
                  onClick={handleGeneratePlan}
                  disabled={isPlanning}
                  title="Regenerate plan from current analysis"
                >
                  {isPlanning ? <><span className="spinner-sm"></span> Planning…</> : <><span>🔄</span> Regenerate Plan</>}
                </button>
                {organizationPlan && organizationPlan.operations.length > 0 && (
                  <button
                    id="top-proceed-to-review-btn"
                    className="btn btn-primary"
                    onClick={handleGoToReview}
                    title="Proceed to review and approve planned operations"
                  >
                    <span>✓</span> Review &amp; Approve →
                  </button>
                )}
              </div>
            </div>

            {isPlanning && (
              <div className="loading-state card" style={{ padding: '3rem', textAlign: 'center' }}>
                <div className="spinner"></div>
                <h3 style={{ marginTop: '1rem', marginBottom: '0.5rem' }}>Generating Organization Plan…</h3>
                <p className="text-muted" style={{ fontSize: '0.85rem' }}>
                  Creating folder mapping, resolving naming conflicts, and checking destination safety…
                </p>
              </div>
            )}

            {planError && !isPlanning && (
              <div className="alert alert-error">
                <span>⚠</span>
                <div>
                  <strong>Plan Generation Error:</strong> {planError}
                  <div style={{ marginTop: '0.5rem' }}>
                    <button className="btn btn-secondary" onClick={handleGeneratePlan} style={{ marginRight: '0.5rem' }}>
                      Retry Plan Generation
                    </button>
                    <button className="btn btn-secondary" onClick={() => setStep('analyze')}>
                      ← Back to Analysis
                    </button>
                  </div>
                </div>
              </div>
            )}

            {!isPlanning && !planError && !organizationPlan && (
              <div className="step-card">
                <div className="step-card-icon">📋</div>
                <h3 className="step-card-title">Ready to Generate Plan</h3>
                <p className="step-card-desc">Click below to produce a reviewable organization plan.</p>
                <button className="btn btn-primary btn-large" onClick={handleGeneratePlan}>
                  <span>⚡</span> Generate Plan
                </button>
              </div>
            )}

            {!isPlanning && organizationPlan && (
              <>
                {/* Empty Plan Warning */}
                {organizationPlan.operations.length === 0 ? (
                  <div className="step-card">
                    <div className="step-card-icon">ℹ️</div>
                    <h3 className="step-card-title">No Organization Operations Needed</h3>
                    <p className="step-card-desc">
                      {organizationPlan.warnings.length > 0
                        ? organizationPlan.warnings.join(' ')
                        : 'No valid file reorganization actions were identified for this folder.'}
                    </p>
                    <div className="action-row">
                      <button className="btn btn-secondary" onClick={() => setStep('analyze')}>
                        ← Back to Analysis
                      </button>
                      <button className="btn btn-secondary" onClick={() => setStep('scan')}>
                        ← Back to Scan
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {/* Plan Summary Row */}
                    <div className="stats-row">
                      <div className="stat-card">
                        <div className="stat-number">{organizationPlan.operations.length}</div>
                        <div className="stat-label">Total Operations</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">
                          {organizationPlan.operations.filter(o => o.type === 'CREATE_FOLDER').length}
                        </div>
                        <div className="stat-label">Folder Creations</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">
                          {organizationPlan.operations.filter(o => o.type === 'MOVE').length}
                        </div>
                        <div className="stat-label">File Copies / Moves</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-number">
                          {organizationPlan.operations.filter(o => o.type === 'RENAME').length}
                        </div>
                        <div className="stat-label">File Renames</div>
                      </div>
                    </div>

                    {/* Output Location Card */}
                    <div className="output-location-card">
                      <div className="output-location-icon">📁</div>
                      <div>
                        <div className="output-location-label">Target Organization Root (Originals Stay Untouched):</div>
                        <code className="output-location-path">
                          {folderStatus.authorized_root}
                          {folderStatus.authorized_root && !folderStatus.authorized_root.endsWith('/') && !folderStatus.authorized_root.endsWith('\\') ? '\\' : ''}
                          SmartSort_Organized\
                        </code>
                      </div>
                    </div>

                    {/* Proposed Folder Structure */}
                    <div className="categories-card">
                      <h3 className="card-title-sm">Proposed Folder Hierarchy</h3>
                      <div className="category-chips">
                        {proposedFolders.map(folder => (
                          <div key={folder} className="category-chip" style={{ fontFamily: 'monospace' }}>
                            <span style={{ color: 'var(--accent-light)' }}>📁 {folder}</span>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Planned Actions List */}
                    <div className="operations-section">
                      <div className="ops-header">
                        <h3 className="card-title-sm">
                          Planned Actions ({organizationPlan.operations.filter(op => op.type !== 'CREATE_FOLDER').length} files, {organizationPlan.operations.filter(op => op.type === 'CREATE_FOLDER').length} folders)
                        </h3>
                        <span className="hint-text">
                          You will review and approve these individually in the next step.
                        </span>
                      </div>

                      <div className="ops-list">
                        {organizationPlan.operations.map((op, idx) => {
                          const isFolder = op.type === 'CREATE_FOLDER';
                          return (
                            <div key={`${op.type}-${op.destination}-${idx}`} className="op-item op-pending">
                              <div className="op-icon">
                                {isFolder ? '📁' : op.type === 'MOVE' ? '→' : '✏️'}
                              </div>
                              <div className="op-body">
                                <div className="op-paths">
                                  {!isFolder && (
                                    <>
                                      <span className="op-source" title={op.source}>
                                        {op.source.split(/[/\\]/).pop()}
                                      </span>
                                      <span className="op-arrow">→</span>
                                    </>
                                  )}
                                  <span className="op-dest" title={op.destination}>
                                    {op.destination}
                                  </span>
                                </div>
                                {op.reason && (
                                  <div className="op-reason">{op.reason}</div>
                                )}
                              </div>
                              <div className="op-status">
                                <span className="category-chip" style={{ fontSize: '0.72rem' }}>
                                  {opTypeLabel(op.type)}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* Warnings (if any) */}
                    {organizationPlan.warnings.length > 0 && (
                      <details className="warnings-details">
                        <summary>⚠ {organizationPlan.warnings.length} warning{organizationPlan.warnings.length !== 1 ? 's' : ''} detected</summary>
                        <ul className="warnings-list">
                          {organizationPlan.warnings.map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                      </details>
                    )}

                    {/* Navigation Buttons */}
                    <div className="action-row" style={{ marginTop: '1rem' }}>
                      <button
                        id="proceed-to-review-btn"
                        className="btn btn-primary btn-large"
                        onClick={handleGoToReview}
                      >
                        <span>✓</span> Proceed to Review &amp; Approvals →
                      </button>
                      <button className="btn btn-secondary" onClick={() => setStep('analyze')}>
                        ← Back to Analysis
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
          </section>
        )}

        {/* ══ STEP 5: Review & Approve ═══════════════════════════════════════ */}
        {step === 'review' && organizationPlan && (
          <section className="review-section">
            {/* Summary header */}
            <div className="review-header">
              <div>
                <h2 className="section-title">Review &amp; Approve Plan</h2>
                <p className="section-desc">
                  SmartSort will copy your files into the <strong>SmartSort_Organized</strong> folder.
                  Your original files stay untouched. Approve the actions you want, reject the ones you don't.
                </p>
              </div>
              <div className="action-buttons-top">
                <button
                  id="top-execute-plan-btn"
                  className={`btn btn-primary btn-execute ${approvedCount > 0 ? 'pulse-glow' : ''}`}
                  onClick={handleExecutePlan}
                  disabled={approvedCount === 0 || isExecuting}
                  title={approvedCount === 0 ? 'Approve some actions first' : 'Copy approved files to SmartSort_Organized'}
                >
                  {isExecuting
                    ? <><span className="spinner-sm"></span> Organizing…</>
                    : <><span>🗂️</span> Organize Files ({approvedCount})</>}
                </button>
              </div>
            </div>

            {/* Output location preview */}
            <div className="output-location-card">
              <div className="output-location-icon">📁</div>
              <div>
                <div className="output-location-label">Organized files will be saved to:</div>
                <code className="output-location-path">
                  {folderStatus.authorized_root}
                  {folderStatus.authorized_root && !folderStatus.authorized_root.endsWith('/') && !folderStatus.authorized_root.endsWith('\\') ? '\\' : ''}
                  SmartSort_Organized\
                </code>
              </div>
            </div>

            {/* Approval summary */}
            <div className="approval-summary">
              <div className="approval-count-row">
                <div className="count-chip chip-pending">
                  <span className="chip-dot">○</span>
                  <strong>{pendingCount}</strong> Pending
                </div>
                <div className="count-chip chip-approved">
                  <span className="chip-dot">✓</span>
                  <strong>{approvedCount}</strong> Approved
                </div>
                <div className="count-chip chip-rejected">
                  <span className="chip-dot">✕</span>
                  <strong>{rejectedCount}</strong> Rejected
                </div>
                <div className="count-chip chip-total">
                  <strong>{totalOps}</strong> Total
                </div>
              </div>

              {/* Action Toolbar: Approve, Reject, Back, and Organize */}
              <div className="bulk-action-row">
                <div className="bulk-actions-left">
                  <button
                    id="approve-all-btn"
                    className="btn btn-approve-all"
                    onClick={handleApproveAll}
                    disabled={isBulkApproving || isBulkRejecting || pendingCount === 0}
                    title={pendingCount === 0 ? 'No pending actions to approve' : `Approve all ${pendingCount} pending actions`}
                  >
                    {isBulkApproving ? <><span className="spinner-sm"></span> Approving…</> : <><span>✓</span> Approve All</>}
                  </button>
                  <button
                    id="reject-all-btn"
                    className="btn btn-reject-all"
                    onClick={() => {
                      if (approvedCount > 0) {
                        setShowRejectAllConfirm(true);
                      } else {
                        handleRejectAll();
                      }
                    }}
                    disabled={isBulkApproving || isBulkRejecting || (pendingCount === 0 && approvedCount === 0)}
                    title={pendingCount === 0 && approvedCount === 0 ? 'No actions to reject' : 'Reject all pending and approved actions'}
                  >
                    {isBulkRejecting ? <><span className="spinner-sm"></span> Rejecting…</> : <><span>✕</span> Reject All</>}
                  </button>
                </div>

                <div className="bulk-actions-right">
                  <button
                    className="btn btn-secondary"
                    onClick={() => setStep('plan')}
                  >
                    ← Back to Plan
                  </button>
                  <button
                    id="summary-execute-plan-btn"
                    className={`btn btn-primary btn-execute ${approvedCount > 0 ? 'pulse-glow' : ''}`}
                    onClick={handleExecutePlan}
                    disabled={approvedCount === 0 || isExecuting}
                    title={approvedCount === 0 ? 'Approve some actions first' : 'Copy approved files to SmartSort_Organized'}
                  >
                    {isExecuting
                      ? <><span className="spinner-sm"></span> Organizing…</>
                      : <><span>🗂️</span> Organize Files ({approvedCount} approved)</>}
                  </button>
                </div>
              </div>

              {pendingCount === 0 && approvedCount === 0 && rejectedCount === 0 && (
                <div className="empty-state-sm">No actions to review.</div>
              )}
            </div>

            {/* Reject All confirmation dialog */}
            {showRejectAllConfirm && (
              <div className="confirm-overlay">
                <div className="confirm-dialog">
                  <h3>Reject all actions?</h3>
                  <p>This will reject all {approvedCount + pendingCount} pending and approved actions, including {approvedCount} that were already approved.</p>
                  <div className="confirm-buttons">
                    <button className="btn btn-danger" onClick={handleRejectAll}>Yes, Reject All</button>
                    <button className="btn btn-secondary" onClick={() => setShowRejectAllConfirm(false)}>Cancel</button>
                  </div>
                </div>
              </div>
            )}

            {approvalError && (
              <div className="alert alert-error" style={{ marginBottom: '1rem' }}>
                <span>⚠</span> <div>{approvalError}</div>
              </div>
            )}

            {/* Category breakdown */}
            {analysisResult && Object.keys(analysisResult.categories).length > 0 && (
              <div className="categories-card">
                <h3 className="card-title-sm">File Categories</h3>
                <div className="category-chips">
                  {Object.entries(analysisResult.categories).sort((a,b) => b[1]-a[1]).map(([cat, count]) => (
                    <div key={cat} className="category-chip">
                      <span className="cat-name">{cat}</span>
                      <span className="cat-count">{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Operations list */}
            {approvalStatus && (
              <div className="operations-section">
                <div className="ops-header">
                  <h3 className="card-title-sm">
                    Review Actions ({approvalStatus.operations.filter(op => op.type !== 'CREATE_FOLDER').length} files, {approvalStatus.operations.filter(op => op.type === 'CREATE_FOLDER').length} folders)
                  </h3>
                  <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                    <button
                      className="btn-text"
                      onClick={() => setShowOpsDetail(v => !v)}
                    >
                      {showOpsDetail ? 'Hide details' : 'Show details'}
                    </button>
                  </div>
                </div>

                {showOpsDetail && (
                  <>
                    {/* Fast Filter & Search Bar */}
                    <div className="ops-filter-bar">
                      <div className="ops-filter-tabs">
                        <button
                          type="button"
                          className={`ops-filter-tab ${approvalFilter === 'all' ? 'active' : ''}`}
                          onClick={() => setApprovalFilter('all')}
                        >
                          All ({totalOps})
                        </button>
                        <button
                          type="button"
                          className={`ops-filter-tab tab-pending ${approvalFilter === 'pending' ? 'active' : ''}`}
                          onClick={() => setApprovalFilter('pending')}
                        >
                          ○ Pending ({pendingCount})
                        </button>
                        <button
                          type="button"
                          className={`ops-filter-tab tab-approved ${approvalFilter === 'approved' ? 'active' : ''}`}
                          onClick={() => setApprovalFilter('approved')}
                        >
                          ✓ Approved ({approvedCount})
                        </button>
                        <button
                          type="button"
                          className={`ops-filter-tab tab-rejected ${approvalFilter === 'rejected' ? 'active' : ''}`}
                          onClick={() => setApprovalFilter('rejected')}
                        >
                          ✕ Rejected ({rejectedCount})
                        </button>
                        <button
                          type="button"
                          className={`ops-filter-tab tab-needs-review ${approvalFilter === 'needs_review' ? 'active' : ''}`}
                          onClick={() => setApprovalFilter('needs_review')}
                        >
                          ⚠ Needs Review ({needsReviewCount})
                        </button>
                      </div>

                      <div className="ops-filter-search">
                        <input
                          type="text"
                          placeholder="Filter actions by path or reason…"
                          className="text-input text-input-sm"
                          value={approvalSearchQuery}
                          onChange={e => setApprovalSearchQuery(e.target.value)}
                        />
                        {approvalSearchQuery && (
                          <button
                            type="button"
                            className="btn-clear-search"
                            onClick={() => setApprovalSearchQuery('')}
                            title="Clear search"
                          >
                            ✕
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="ops-list">
                      {filteredApprovalOps.length === 0 ? (
                        <div className="empty-state-sm" style={{ padding: '2.5rem', textAlign: 'center' }}>
                          <p style={{ color: 'var(--text-muted)' }}>No actions match the selected filter or search.</p>
                          {(approvalFilter !== 'all' || approvalSearchQuery) && (
                            <button
                              className="btn btn-secondary btn-sm"
                              style={{ marginTop: '0.75rem' }}
                              onClick={() => {
                                setApprovalFilter('all');
                                setApprovalSearchQuery('');
                              }}
                            >
                              Reset filters
                            </button>
                          )}
                        </div>
                      ) : (
                        filteredApprovalOps.map((op) => {
                          const isApproved = op.approval_decision === 'APPROVED';
                          const isRejected = op.approval_decision === 'REJECTED';
                          const isDeciding = decidingOp === op.operation_id;
                          const isFolder = op.type === 'CREATE_FOLDER';

                          return (
                            <div
                              key={op.operation_id}
                              className={`op-item ${isApproved ? 'op-approved' : isRejected ? 'op-rejected' : 'op-pending'} ${isFolder ? 'op-folder' : ''}`}
                            >
                              <div className="op-icon">
                                {isFolder ? '📁' :
                                 (op.type === 'MOVE' || op.type === 'COPY_FILE') ? '→' : '✏️'}
                              </div>
                              <div className="op-body">
                                <div className="op-paths">
                                  {!isFolder && (
                                    <>
                                      <span className="op-source">{op.source.split(/[/\\]/).pop()}</span>
                                      <span className="op-arrow">→</span>
                                    </>
                                  )}
                                  <span className="op-dest">{op.destination}</span>
                                </div>
                                {op.reason && (
                                  <div className="op-reason">{op.reason}</div>
                                )}
                                <div className="op-meta-row" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.25rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                                  {op.confidence && (
                                    <span className="badge-confidence" style={{ fontWeight: 600 }}>
                                      Confidence: {op.confidence}{op.confidence_score !== undefined && op.confidence_score !== null ? ` (${Math.round(op.confidence_score * 100)}%)` : ''}
                                    </span>
                                  )}
                                  {op.flags && (
                                    <span className="badge-flags" style={{ background: 'rgba(234, 179, 8, 0.15)', color: '#ca8a04', padding: '0.1rem 0.4rem', borderRadius: '4px' }}>
                                      flags: {op.flags}
                                    </span>
                                  )}
                                  {op.likely_newer && (
                                    <span className="badge-newer" style={{ background: 'rgba(59, 130, 246, 0.15)', color: '#2563eb', padding: '0.1rem 0.4rem', borderRadius: '4px' }}>
                                      likely newer
                                    </span>
                                  )}
                                </div>
                                {!op.is_eligible && op.ineligibility_reason && (
                                  <div className="op-ineligible">⚠ {op.ineligibility_reason}</div>
                                )}
                              </div>
                              <div className="op-status">
                                {isApproved && <span className="badge-approved">✓ Approved</span>}
                                {isRejected && <span className="badge-rejected">✕ Rejected</span>}
                                {!isApproved && !isRejected && <span className="badge-pending">Pending</span>}
                              </div>
                              {!isFolder && (
                                <div className="op-controls">
                                  <button
                                    id={`approve-op-${op.operation_id}`}
                                    className={`btn-op approve ${isApproved ? 'active' : ''}`}
                                    disabled={!op.is_eligible || isDeciding || isBulkApproving || isBulkRejecting}
                                    onClick={() => handleDecideOperation(op.plan_id, op.operation_id, 'APPROVED')}
                                    title="Approve this action"
                                  >
                                    {isDeciding ? '…' : '✓'}
                                  </button>
                                  <button
                                    id={`reject-op-${op.operation_id}`}
                                    className={`btn-op reject ${isRejected ? 'active' : ''}`}
                                    disabled={!op.is_eligible || isDeciding || isBulkApproving || isBulkRejecting}
                                    onClick={() => handleDecideOperation(op.plan_id, op.operation_id, 'REJECTED')}
                                    title="Reject this action"
                                  >
                                    {isDeciding ? '…' : '✕'}
                                  </button>
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Warnings */}
            {organizationPlan.warnings.length > 0 && (
              <details className="warnings-details">
                <summary>⚠ {organizationPlan.warnings.length} warning{organizationPlan.warnings.length !== 1 ? 's' : ''}</summary>
                <ul className="warnings-list">
                  {organizationPlan.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </details>
            )}

            {/* Execute section */}
            <div className="execute-section">
              <div className="execute-info">
                <strong>
                  {approvedCount === 0
                    ? 'Approve at least one action above to continue.'
                    : `${approvedCount} action${approvedCount !== 1 ? 's' : ''} approved and ready to organize.`}
                </strong>
                <p className="hint-text">
                  Rejected actions are skipped. Approved files will be <em>copied</em> to SmartSort_Organized — originals stay safe.
                </p>
              </div>
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                <button
                  className="btn btn-secondary"
                  onClick={() => setStep('plan')}
                >
                  ← Back to Plan
                </button>
                <button
                  id="execute-plan-btn"
                  className="btn btn-primary btn-large btn-execute"
                  onClick={handleExecutePlan}
                  disabled={approvedCount === 0 || isExecuting}
                  title={approvedCount === 0 ? 'Approve some actions first' : 'Copy approved files to SmartSort_Organized'}
                >
                  {isExecuting
                    ? <><span className="spinner-sm"></span> Organizing…</>
                    : <><span>🗂️</span> Organize Files ({approvedCount} approved)</>}
                </button>
              </div>
              {executionError && (
                <div className="alert alert-error" style={{ marginTop: '0.75rem' }}>
                  <span>⚠</span> <div>{executionError}</div>
                </div>
              )}
            </div>
          </section>
        )}

        {/* ══ STEP 6: Done ══════════════════════════════════════════════════ */}
        {step === 'done' && executionStatus && (
          <section className="step-card done-card">
            <div className="done-icon">
              {executionStatus.failed_count > 0 ? '⚠️' : '🎉'}
            </div>
            <h2 className="step-card-title">
              {executionStatus.failed_count > 0 ? 'Done with some issues' : 'Files Organized!'}
            </h2>

            <div className="stats-row">
              <div className="stat-card stat-success">
                <div className="stat-number">{executionStatus.completed_count}</div>
                <div className="stat-label">Copied successfully</div>
              </div>
              {executionStatus.skipped_count > 0 && (
                <div className="stat-card stat-warning">
                  <div className="stat-number">{executionStatus.skipped_count}</div>
                  <div className="stat-label">Skipped (rejected or not approved)</div>
                </div>
              )}
              {executionStatus.failed_count > 0 && (
                <div className="stat-card stat-error">
                  <div className="stat-number">{executionStatus.failed_count}</div>
                  <div className="stat-label">Failed</div>
                </div>
              )}
            </div>

            {/* Output folder location */}
            {outputFolderInfo && (
              <div className="output-result-card">
                <div className="output-result-icon">📂</div>
                <div>
                  <div className="output-result-label">Your organized files are in:</div>
                  <code className="output-result-path">{outputFolderInfo.output_folder_path}</code>
                  {outputFolderInfo.exists ? (
                    <div className="output-exists">✓ Folder created successfully</div>
                  ) : (
                    <div className="output-missing">⚠ Folder not found — check for errors above</div>
                  )}
                </div>
              </div>
            )}

            <p className="done-note">
              🔒 Your original files are <strong>untouched</strong> in <code>{folderStatus.authorized_root?.split(/[/\\]/).pop()}</code>.
              Only copies were made into SmartSort_Organized.
            </p>

            {/* Verification */}
            {!verificationStatus && (
              <button
                className="btn btn-secondary"
                onClick={handleVerify}
                disabled={isVerifying}
              >
                {isVerifying ? <><span className="spinner-sm"></span> Verifying…</> : '🔍 Verify Organization'}
              </button>
            )}
            {verificationStatus && (
              <div className="verify-result">
                <strong>Verification:</strong>{' '}
                <span className={verificationStatus.failed_count === 0 ? 'text-success' : 'text-warning'}>
                  {verificationStatus.verified_count} verified, {verificationStatus.failed_count} failed, {verificationStatus.unverified_count} unverified
                </span>
              </div>
            )}

            <div className="done-actions">
              <button
                className="btn btn-primary"
                onClick={() => {
                  setScanResult(null);
                  setAnalysisResult(null);
                  setOrganizationPlan(null);
                  setApprovalStatus(null);
                  setExecutionStatus(null);
                  setVerificationStatus(null);
                  setOutputFolderInfo(null);
                  setStep('scan');
                }}
              >
                Organize Another Folder
              </button>
              <button className="btn btn-secondary" onClick={handleClearFolder}>
                Change Folder
              </button>
            </div>

            {/* Failed operations detail */}
            {executionStatus.executions.some(e => e.status === 'FAILED') && (
              <details className="warnings-details" style={{ marginTop: '1rem' }}>
                <summary>Failed operations ({executionStatus.failed_count})</summary>
                <ul className="warnings-list">
                  {executionStatus.executions
                    .filter(e => e.status === 'FAILED')
                    .map(e => (
                      <li key={e.operation_id}>
                        Operation #{e.operation_id}: {e.error_message || 'Unknown error'}
                      </li>
                    ))}
                </ul>
              </details>
            )}
          </section>
        )}

      </main>

      <footer className="app-footer">
        SmartSort — Organized files are copied to <strong>SmartSort_Organized</strong> · Originals are never modified
      </footer>
    </div>
  );
}
