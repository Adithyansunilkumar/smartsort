/**
 * API service for SmartSort frontend.
 * Communicates with the local FastAPI backend.
 *
 * Phase 2 additions: scanner interfaces and startScan().
 */

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
}

export type BackendConnectionStatus = 'checking' | 'connected' | 'unavailable' | 'malformed';

export interface HealthCheckResult {
  status: BackendConnectionStatus;
  data?: HealthResponse;
  error?: string;
  latencyMs?: number;
  checkedAt: string;
}

export interface FolderStatusResponse {
  is_configured: boolean;
  authorized_root: string | null;
  message: string;
}

export interface SelectFolderResponse {
  status: string;
  authorized_root: string;
  message: string;
}

export interface FolderOperationResult {
  success: boolean;
  data?: FolderStatusResponse | SelectFolderResponse;
  error?: string;
  errorCode?: string;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

/**
 * Fetch health status from the backend.
 * Handles network errors, non-200 responses, and malformed payloads gracefully.
 */
export async function fetchHealth(): Promise<HealthCheckResult> {
  const startTime = performance.now();
  const checkedAt = new Date().toLocaleTimeString();

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);

    const response = await fetch(`${API_BASE_URL}/api/health`, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
      },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);
    const latencyMs = Math.round(performance.now() - startTime);

    if (!response.ok) {
      return {
        status: 'unavailable',
        error: `Server returned HTTP ${response.status} (${response.statusText || 'Error'})`,
        latencyMs,
        checkedAt,
      };
    }

    let rawData: unknown;
    try {
      rawData = await response.json();
    } catch {
      return {
        status: 'malformed',
        error: 'Backend returned non-JSON or invalid JSON response',
        latencyMs,
        checkedAt,
      };
    }

    if (
      typeof rawData === 'object' &&
      rawData !== null &&
      'status' in rawData &&
      typeof (rawData as Record<string, unknown>).status === 'string' &&
      'app' in rawData &&
      typeof (rawData as Record<string, unknown>).app === 'string'
    ) {
      const data = rawData as HealthResponse;
      return {
        status: 'connected',
        data,
        latencyMs,
        checkedAt,
      };
    }

    return {
      status: 'malformed',
      error: 'Backend response schema missing required health fields (status, app)',
      latencyMs,
      checkedAt,
    };
  } catch (err: unknown) {
    const latencyMs = Math.round(performance.now() - startTime);
    let errorMessage = 'Could not connect to backend server';

    if (err instanceof Error) {
      if (err.name === 'AbortError') {
        errorMessage = 'Connection timed out (backend not responding)';
      } else {
        errorMessage = err.message;
      }
    }

    return {
      status: 'unavailable',
      error: errorMessage,
      latencyMs,
      checkedAt,
    };
  }
}

/**
 * Retrieve the currently authorized root from the backend.
 */
export async function fetchCurrentFolder(): Promise<FolderOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/folder`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });

    if (!response.ok) {
      return {
        success: false,
        error: `Failed to fetch current folder: HTTP ${response.status}`,
      };
    }

    const data: FolderStatusResponse = await response.json();
    return {
      success: true,
      data,
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error fetching folder status',
    };
  }
}

/**
 * Authorize a local directory root on the backend.
 */
export async function selectFolder(path: string): Promise<FolderOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/folder/select`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({ path }),
    });

    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = responseBody?.detail;
      const errorMessage =
        typeof detail === 'object' && detail?.message
          ? detail.message
          : typeof detail === 'string'
          ? detail
          : `Server returned HTTP ${response.status}`;
      const errorCode = typeof detail === 'object' ? detail?.code : undefined;

      return {
        success: false,
        error: errorMessage,
        errorCode,
      };
    }

    return {
      success: true,
      data: responseBody as SelectFolderResponse,
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error establishing authorized root',
    };
  }
}

/**
 * Clear the currently authorized root on the backend.
 */
export async function clearFolder(): Promise<FolderOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/folder`, {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    });

    if (!response.ok) {
      return {
        success: false,
        error: `Failed to clear folder: HTTP ${response.status}`,
      };
    }

    return { success: true };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error clearing folder',
    };
  }
}

/**
 * Upload a folder's files and establish it as the authorized root on the backend.
 */
export async function uploadFolder(files: FileList | File[]): Promise<FolderOperationResult> {
  try {
    const formData = new FormData();
    const relativePaths: string[] = [];

    const fileList = Array.from(files);
    if (fileList.length === 0) {
      return { success: false, error: 'No files selected to upload.' };
    }

    for (const file of fileList) {
      formData.append('files', file);
      const relPath = file.webkitRelativePath || file.name;
      relativePaths.push(relPath);
    }
    formData.append('relative_paths', JSON.stringify(relativePaths));

    const response = await fetch(`${API_BASE_URL}/api/folder/upload`, {
      method: 'POST',
      body: formData,
    });

    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = responseBody?.detail;
      const message = typeof detail === 'object' && detail?.message ? detail.message : (detail || `Upload failed (HTTP ${response.status})`);
      return {
        success: false,
        error: message,
        errorCode: typeof detail === 'object' ? detail?.code : undefined,
      };
    }

    return {
      success: true,
      data: responseBody as SelectFolderResponse,
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error during folder upload',
    };
  }
}

/**
 * Open native system directory picker dialog on the host machine.
 */
export async function browseNativeFolder(): Promise<{ success: boolean; path?: string; error?: string }> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/folder/browse`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });

    if (!response.ok) {
      return { success: false, error: `Failed to open directory dialog (HTTP ${response.status})` };
    }

    const data = await response.json();
    if (data.status === 'success' && data.path) {
      return { success: true, path: data.path };
    }
    return { success: false, error: data.message || 'No folder selected' };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Error launching directory browser',
    };
  }
}

// ============================================================================
// Phase 2 � Filesystem Scanner
// ============================================================================

/** Metadata for a single discovered file, as returned by POST /api/scan. */
export interface ScannedFile {
  name: string;
  relative_path: string;
  extension: string;
  size_bytes: number;
  modified_at: string;
  mime_type: string | null;
}

/** Metadata for a single discovered directory. */
export interface ScannedDirectory {
  name: string;
  relative_path: string;
}

/** Record of a filesystem entry skipped during scanning. */
export interface ScanSkip {
  relative_path: string;
  reason: string;
  detail?: string | null;
}

/** Complete result of POST /api/scan. */
export interface ScanResult {
  scan_id: string;
  status: 'completed' | 'failed';
  root_display: string;
  file_count: number;
  directory_count: number;
  skip_count: number;
  scanned_at: string;
  files: ScannedFile[];
  directories: ScannedDirectory[];
  skips: ScanSkip[];
  error: string | null;
}

/** Result wrapper for startScan(). */
export interface ScanOperationResult {
  success: boolean;
  data?: ScanResult;
  error?: string;
  errorCode?: string;
}

/**
 * Initiate a filesystem scan on the currently authorized root.
 * No path is supplied from the frontend � the backend always uses its
 * configured authorized root.
 */
export async function startScan(): Promise<ScanOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/scan`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });

    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = responseBody?.detail;
      const errorMessage =
        typeof detail === 'object' && detail?.message
          ? detail.message
          : typeof detail === 'string'
          ? detail
          : `Server returned HTTP ${response.status}`;
      const errorCode =
        typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: errorMessage, errorCode };
    }

    if (
      typeof responseBody === 'object' &&
      responseBody !== null &&
      'scan_id' in responseBody &&
      'status' in responseBody
    ) {
      return { success: true, data: responseBody as ScanResult };
    }

    return {
      success: false,
      error: 'Backend returned an unexpected scan response shape',
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error while starting scan',
    };
  }
}

// ============================================================================
// Phase 4 — Duplicate Detection
// ============================================================================

export interface DuplicateFile {
  file_id: number;
  name: string;
  relative_path: string;
  size_bytes: number;
  modified_at: string;
}

export interface DuplicateGroup {
  hash_digest: string;
  size_bytes: number;
  files: DuplicateFile[];
}

export interface DuplicateResult {
  scan_id: string;
  total_groups: number;
  total_duplicate_files: number;
  groups: DuplicateGroup[];
}

export interface DuplicateOperationResult {
  success: boolean;
  data?: DuplicateResult;
  error?: string;
  errorCode?: string;
}

/**
 * Identify exact duplicate files for a completed scan.
 */
export async function findDuplicates(scanId: string): Promise<DuplicateOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/duplicates/${scanId}`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });

    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = responseBody?.detail;
      const errorMessage =
        typeof detail === 'object' && detail?.message
          ? detail.message
          : typeof detail === 'string'
          ? detail
          : `Server returned HTTP ${response.status}`;
      const errorCode =
        typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: errorMessage, errorCode };
    }

    if (
      typeof responseBody === 'object' &&
      responseBody !== null &&
      'total_groups' in responseBody &&
      'groups' in responseBody
    ) {
      return { success: true, data: responseBody as DuplicateResult };
    }

    return {
      success: false,
      error: 'Backend returned an unexpected duplicates response shape',
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error while detecting duplicates',
    };
  }
}

// ============================================================================
// Phase 5 — Document Content Extraction
// ============================================================================

export interface ExtractedDocument {
  file_id: number;
  relative_path: string;
  status: string;
  character_count?: number | null;
  page_count?: number | null;
  error_info?: string | null;
}

export interface ExtractionResult {
  scan_id: string;
  total_processed: number;
  successful: number;
  empty: number;
  failed: number;
  unsupported: number;
  documents: ExtractedDocument[];
}

export interface ExtractionOperationResult {
  success: boolean;
  data?: ExtractionResult;
  error?: string;
  errorCode?: string;
}

/**
 * Extract text from supported documents in a completed scan.
 */
export async function extractContent(scanId: string): Promise<ExtractionOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/extract/${scanId}`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });

    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = responseBody?.detail;
      const errorMessage =
        typeof detail === 'object' && detail?.message
          ? detail.message
          : typeof detail === 'string'
          ? detail
          : `Server returned HTTP ${response.status}`;
      const errorCode =
        typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: errorMessage, errorCode };
    }

    if (
      typeof responseBody === 'object' &&
      responseBody !== null &&
      'total_processed' in responseBody
    ) {
      return { success: true, data: responseBody as ExtractionResult };
    }

    return {
      success: false,
      error: 'Backend returned an unexpected extraction response shape',
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error while extracting documents',
    };
  }
}



// ============================================================================
// Phase 6 — File Analysis
// ============================================================================

export interface FileAnalysisResult {
  file_id: number;
  relative_path: string;
  category: string;
  size_bytes: number;
  size_bucket: string;
  age_days: number;
  age_bucket: string;
  name_tokens: string[];
  is_copy: boolean;
  is_temp: boolean;
  numeric_suffix: number | null;
  depth: number;
  is_root_level: boolean;
  parent_dir: string | null;
  path_tokens: string[];
  has_duplicates: boolean;
  char_count: number | null;
  word_count: number | null;
  line_count: number | null;
  reading_time_mins: number | null;
}

export interface AnalysisSummary {
  scan_id: string;
  total_analyzed: number;
  total_size_bytes: number;
  categories: Record<string, number>;
  age_buckets: Record<string, number>;
  size_buckets: Record<string, number>;
  duplicates_found: number;
  content_analyzed: number;
  temp_or_copy_files: number;
  documents: FileAnalysisResult[];
}

export interface AnalysisOperationResult {
  success: boolean;
  data?: AnalysisSummary;
  error?: string;
  errorCode?: string;
}

/**
 * Run deterministic file analysis on a completed scan.
 */
export async function analyzeFiles(scanId: string): Promise<AnalysisOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/analyze/${scanId}`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });

    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = responseBody?.detail;
      const errorMessage =
        typeof detail === 'object' && detail?.message
          ? detail.message
          : typeof detail === 'string'
          ? detail
          : `Server returned HTTP ${response.status}`;
      const errorCode =
        typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: errorMessage, errorCode };
    }

    if (
      typeof responseBody === 'object' &&
      responseBody !== null &&
      'total_analyzed' in responseBody
    ) {
      return { success: true, data: responseBody as AnalysisSummary };
    }

    return {
      success: false,
      error: 'Backend returned an unexpected analysis response shape',
    };
  } catch (err: unknown) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Network error while analyzing files',
    };
  }
}

// ============================================================================
// Phase 7 - Search & Similarity
// ============================================================================

export interface SearchResult {
  file_id: number;
  scan_id: string;
  name: string;
  relative_path: string;
  extension: string;
  category: string | null;
  size_bytes: number;
  modified_at: string;
  relevance_score: number;
  similarity_score: number;
  lexical_score: number;
  matched_fields: string[];
  matched_terms: string[];
  content_available: boolean;
  has_duplicates: boolean;
}

export interface SearchResponse {
  scan_id: string;
  query: string;
  total_results: number;
  results: SearchResult[];
}

export interface SimilarFile {
  file_id: number;
  name: string;
  relative_path: string;
  extension: string;
  category: string | null;
  size_bytes: number;
  modified_at: string;
  similarity_score: number;
}

export interface SimilarFilesResponse {
  scan_id: string;
  target_file_id: number;
  target_name: string;
  similar_files: SimilarFile[];
}

export interface SearchOperationResult {
  success: boolean;
  data?: SearchResponse;
  error?: string;
  errorCode?: string;
}

export interface SimilarFilesOperationResult {
  success: boolean;
  data?: SimilarFilesResponse;
  error?: string;
  errorCode?: string;
}

export async function searchFiles(scanId: string, query: string, limit = 20): Promise<SearchOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ scan_id: scanId, query, limit }),
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'results' in responseBody) {
      return { success: true, data: responseBody as SearchResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

export async function findSimilarFiles(scanId: string, fileId: number, limit = 10): Promise<SimilarFilesOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/search/similar/${scanId}/${fileId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ limit }),
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'similar_files' in responseBody) {
      return { success: true, data: responseBody as SimilarFilesResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// Phase 8: Organization Engine types & API

export interface OrganizationOperation {
  type: string;
  source: string;
  destination: string;
  reason: string;
  confidence: string;
}

export interface OrganizationPlan {
  plan_id: string;
  scan_id: string;
  objective: string | null;
  created_at: string;
  operations: OrganizationOperation[];
  warnings: string[];
  protected_conflicts: string[];
  summary: Record<string, number>;
}

export interface OrganizationResponse {
  plan: OrganizationPlan;
}

export interface OrganizationOperationResult {
  success: boolean;
  data?: OrganizationResponse;
  error?: string;
  errorCode?: string;
}

export async function generateOrganizationPlan(
  scanId: string,
  objective?: string
): Promise<OrganizationOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/organize/${scanId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ objective: objective || null }),
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'plan' in responseBody) {
      return { success: true, data: responseBody as OrganizationResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

export async function getOrganizationPlan(planId: string): Promise<OrganizationOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/organize/plan/${planId}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'plan' in responseBody) {
      return { success: true, data: responseBody as OrganizationResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// Phase 9: Agent Controller types & API

export type AgentState =
  | 'IDLE'
  | 'SCANNING'
  | 'ANALYZING'
  | 'PLANNING'
  | 'WAITING_FOR_APPROVAL'
  | 'COMPLETED'
  | 'ERROR'
  | 'CANCELLED';

export interface AgentRun {
  run_id: string;
  scan_id: string | null;
  objective: string;
  state: AgentState;
  current_step: string | null;
  progress: number;
  plan_id: string | null;
  created_at: string;
  updated_at: string;
  error: string | null;
}

export interface AgentRunResponse {
  run: AgentRun;
}

export interface AgentOperationResult {
  success: boolean;
  data?: AgentRunResponse;
  error?: string;
  errorCode?: string;
}

export async function startAgentRun(
  objective: string,
  scanId?: string
): Promise<AgentOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/agent/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ objective, scan_id: scanId || null }),
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'run' in responseBody) {
      return { success: true, data: responseBody as AgentRunResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

export async function getAgentRun(runId: string): Promise<AgentOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/agent/runs/${runId}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'run' in responseBody) {
      return { success: true, data: responseBody as AgentRunResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

export async function cancelAgentRun(runId: string): Promise<AgentOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/agent/runs/${runId}/cancel`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'run' in responseBody) {
      return { success: true, data: responseBody as AgentRunResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// ============================================================================
// Phase 11 — Approval System
// ============================================================================

export type ApprovalDecision = 'PENDING' | 'APPROVED' | 'REJECTED';

export interface OperationWithApproval {
  operation_id: number;
  plan_id: string;
  type: string;
  source: string;
  destination: string;
  reason: string | null;
  confidence: string;
  approval_decision: ApprovalDecision;
  comment: string | null;
  decided_at: string | null;
  is_eligible: boolean;
  ineligibility_reason: string | null;
}

export interface PlanApprovalStatus {
  plan_id: string;
  total_operations: number;
  approved_count: number;
  rejected_count: number;
  pending_count: number;
  operations: OperationWithApproval[];
}

export interface ApprovalResponse {
  operation: OperationWithApproval;
  message: string;
}

export interface ApprovalHistoryEntry {
  history_id: number;
  approval_id: number;
  previous_decision: ApprovalDecision;
  new_decision: ApprovalDecision;
  comment: string | null;
  changed_at: string;
}

export interface ApprovalOperationResult {
  success: boolean;
  data?: PlanApprovalStatus;
  error?: string;
  errorCode?: string;
}

export interface ApprovalDecisionResult {
  success: boolean;
  data?: ApprovalResponse;
  error?: string;
  errorCode?: string;
}

export interface ApprovalHistoryResult {
  success: boolean;
  data?: ApprovalHistoryEntry[];
  error?: string;
  errorCode?: string;
}

/** Retrieve approval status for all operations in a plan. */
export async function getPlanApprovals(planId: string): Promise<ApprovalOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/plans/${planId}/approvals`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'plan_id' in responseBody) {
      return { success: true, data: responseBody as PlanApprovalStatus };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

/** Record an APPROVED or REJECTED decision for a single operation.
 *
 * IMPORTANT: This records a human decision for later reference only.
 * No files are changed. Approval does not execute the operation.
 */
export async function decideOperation(
  planId: string,
  operationId: number,
  decision: 'APPROVED' | 'REJECTED',
  comment?: string,
): Promise<ApprovalDecisionResult> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/plans/${planId}/approvals/${operationId}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ decision, comment: comment || null }),
      }
    );
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (typeof responseBody === 'object' && responseBody !== null && 'operation' in responseBody) {
      return { success: true, data: responseBody as ApprovalResponse };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

/** Get the full decision history for a single operation. */
export async function getOperationApprovalHistory(
  planId: string,
  operationId: number,
): Promise<ApprovalHistoryResult> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/plans/${planId}/approvals/${operationId}/history`,
      {
        method: 'GET',
        headers: { Accept: 'application/json' },
      }
    );
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    if (Array.isArray(responseBody)) {
      return { success: true, data: responseBody as ApprovalHistoryEntry[] };
    }
    return { success: false, error: 'Unexpected response shape' };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// ============================================================================
// Phase 12 - Execution Engine
// ============================================================================

export interface OperationExecutionState {
  operation_id: number;
  plan_id: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'SKIPPED';
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface PlanExecutionStatus {
  plan_id: string;
  total_operations: number;
  completed_count: number;
  failed_count: number;
  skipped_count: number;
  in_progress_count: number;
  pending_count: number;
  executions: OperationExecutionState[];
}

export interface ExecutionOperationResult {
  success: boolean;
  data?: PlanExecutionStatus;
  error?: string;
  errorCode?: string;
}

/** Retrieve the execution status of all operations in a plan. */
export async function getPlanExecutions(planId: string): Promise<ExecutionOperationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/plans/${planId}/executions`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    return { success: true, data: responseBody as PlanExecutionStatus };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

/** Execute approved and pending operations in a plan. */
export async function executePlan(
  planId: string,
  operationIds?: number[]
): Promise<ExecutionOperationResult> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/plans/${planId}/execute`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ operation_ids: operationIds || null }),
      }
    );
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    return { success: true, data: responseBody as PlanExecutionStatus };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

export type VerificationStatusEnum = 'VERIFIED' | 'FAILED' | 'INCONCLUSIVE';

export interface OperationVerificationState {
  operation_id: number;
  plan_id: string;
  status: VerificationStatusEnum;
  details?: string;
  verified_at: string;
}

export interface PlanVerificationStatus {
  plan_id: string;
  total_operations: number;
  verified_count: number;
  failed_count: number;
  inconclusive_count: number;
  unverified_count: number;
  verifications: OperationVerificationState[];
}

export interface VerificationResult {
  success: boolean;
  data?: PlanVerificationStatus;
  error?: string;
  errorCode?: string;
}

/** Retrieve the verification status of all operations in a plan. */
export async function getPlanVerifications(planId: string): Promise<VerificationResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/plans/${planId}/verifications`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    return { success: true, data: responseBody as PlanVerificationStatus };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

/** Verify executed operations in a plan without mutating filesystem. */
export async function verifyPlan(planId: string): Promise<VerificationResult> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/plans/${planId}/verify`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      }
    );
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    return { success: true, data: responseBody as PlanVerificationStatus };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// ============================================================================
// Bulk Approval — Approve All / Reject All
// ============================================================================

export interface BulkDecisionResponse {
  updated_count: number;
  skipped_count: number;
  plan_status: PlanApprovalStatus;
  message: string;
}

export interface BulkDecisionResult {
  success: boolean;
  data?: BulkDecisionResponse;
  error?: string;
  errorCode?: string;
}

/** Approve all pending eligible operations in a plan at once. */
export async function bulkApproveOperations(planId: string): Promise<BulkDecisionResult> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/plans/${planId}/approvals/bulk-approve`,
      { method: 'POST', headers: { Accept: 'application/json' } }
    );
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    return { success: true, data: responseBody as BulkDecisionResponse };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

/** Reject all pending operations in a plan at once. */
export async function bulkRejectOperations(planId: string): Promise<BulkDecisionResult> {
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/plans/${planId}/approvals/bulk-reject`,
      { method: 'POST', headers: { Accept: 'application/json' } }
    );
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = responseBody?.detail;
      const msg = typeof detail === 'object' && detail?.message ? detail.message : typeof detail === 'string' ? detail : `HTTP ${response.status}`;
      const code = typeof detail === 'object' && detail?.code ? detail.code : undefined;
      return { success: false, error: msg, errorCode: code };
    }
    return { success: true, data: responseBody as BulkDecisionResponse };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}

// ============================================================================
// Output Folder Info
// ============================================================================

export interface OutputFolderInfo {
  output_folder_name: string;
  output_folder_path: string | null;
  exists: boolean;
  root_path: string | null;
}

export interface OutputFolderResult {
  success: boolean;
  data?: OutputFolderInfo;
  error?: string;
}

/** Get the SmartSort_Organized output folder path and existence status. */
export async function getOutputFolder(): Promise<OutputFolderResult> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/output-folder`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const responseBody = await response.json().catch(() => null);
    if (!response.ok) {
      return { success: false, error: `HTTP ${response.status}` };
    }
    return { success: true, data: responseBody as OutputFolderInfo };
  } catch (err: unknown) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' };
  }
}




