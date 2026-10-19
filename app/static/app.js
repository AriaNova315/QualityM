import { ApiError, apiRequest, saveBlob } from "/static/api.js";

window.qualityMApp = () => ({
  tabs: [
    { id: "projects", label: "项目" }, { id: "import", label: "导入" },
    { id: "audits", label: "审核" }, { id: "settings", label: "设置" },
    { id: "templates", label: "模板库" }, { id: "exports", label: "导出" },
  ],
  activeView: "projects",
  health: { status: "loading", label: "正在检查服务…", version: "—", database: "—" },
  pendingKeys: [], globalError: "", errorCode: "", conflictMessage: "", dirty: false,
  feedback: { type: "success", title: "", message: "" },
  projects: [], selectedVersionId: null,
  selectedFile: null, preview: null, previewToken: "", phaseOverrides: {}, checklistMappings: {}, importConfigNumber: "0.0", saveGlobalMappings: false,
  audits: [], selectedAudit: null, settings: null,
  exportJob: null, exportTimer: null,
  templates: [], templateFile: null, templateDiff: null, templatePreviewToken: "",
  deletePreview: null, deleteConfirmation: "", deleteVersionId: null,

  get productTemplates() { return this.templates.filter((item) => item.audit_kind === "product" && item.enabled); },
  get mappingSubjects() {
    return (this.preview?.product_mapping_resolutions || []).map((item) => item.subject);
  },
  mappingResolution(subject) { return (this.preview?.product_mapping_resolutions || []).find((item) => item.subject === subject) || null; },
  automaticMappingLabel(subject) {
    const resolution = this.mappingResolution(subject);
    return resolution?.template_name ? `自动匹配：${resolution.template_name}` : "自动匹配不可用，请手工选择";
  },
  mappingResolutionLabel(subject) {
    const resolution = this.mappingResolution(subject);
    if (!resolution?.template_name) return "当前没有可用的自动匹配";
    const sources = { builtin_exact: "内置精确规则", builtin_alias: "内置别名规则", user: "已保存的全局精确映射" };
    return `当前自动匹配：${resolution.template_name}（${sources[resolution.source] || "自动规则"}）`;
  },
  get hasUnresolvedMappings() {
    return (this.preview?.product_mapping_resolutions || []).some((item) => !item.stable_key && !this.checklistMappings[item.subject]);
  },
  get previewDiagnostics() { return this.preview?.diagnostics || []; },
  diagnosticText(item) {
    const message = item.message || item.resolution || "请核对来源位置";
    const location = item.location || item.source;
    if (!location) return message;
    const parts = [location.sheet, location.row ? `第 ${location.row} 行` : "", location.column_name || ""].filter(Boolean);
    return parts.length ? `${message}（${parts.join(" ")}）` : message;
  },
  get hasImportBlockers() { return this.previewDiagnostics.some((item) => item.severity === "blocker"); },
  get exportIsActive() { return ["queued", "snapshotting", "building", "validating"].includes(this.exportJob?.status); },
  get exportProgress() { return { queued: 8, snapshotting: 25, building: 58, validating: 85, completed: 100, failed: 100 }[this.exportJob?.status] || 0; },
  get exportStatusLabel() { return { queued: "等待处理", snapshotting: "生成不可变快照", building: "构建工作簿", validating: "校验导出文件", completed: "导出完成", failed: "导出失败" }[this.exportJob?.status] || "尚未开始"; },
  get diffSummary() {
    if (!this.templateDiff) return "";
    const labels = { added: "新增", modified: "修改", disabled: "停用", conflict: "冲突" };
    const counts = this.templateDiff.reduce((result, change) => ({ ...result, [change.kind]: (result[change.kind] || 0) + 1 }), {});
    return Object.entries(counts).map(([kind, count]) => `${labels[kind] || kind} ${count}`).join("，") || "无变化";
  },

  async init() {
    window.addEventListener("beforeunload", (event) => {
      if (!this.dirty) return;
      event.preventDefault(); event.returnValue = "";
    });
    window.addEventListener("unhandledrejection", (event) => {
      event.preventDefault(); this.handleError(event.reason);
    });
    try {
      const payload = await apiRequest("/api/health", { timeout: 8000 });
      this.health = { status: payload.status === "ok" ? "ready" : "degraded", label: payload.status === "ok" ? "服务正常" : "服务受限", version: payload.version, database: payload.database.status };
    } catch (error) {
      this.health = { status: "degraded", label: "无法连接服务", version: "—", database: "—" };
      this.handleError(error);
    }
    await Promise.all([this.loadProjects(), this.loadTemplates()]);
  },

  isPending(key) { return this.pendingKeys.includes(key); },
  async runAction(key, action, onError = null) {
    if (this.isPending(key)) return null;
    this.pendingKeys.push(key); this.clearError();
    try { return await action(); }
    catch (error) { onError ? onError(error) : this.handleError(error); return null; }
    finally { this.pendingKeys = this.pendingKeys.filter((value) => value !== key); }
  },
  showFeedback(type, title, message) {
    this.feedback = { type, title, message };
    if (this.$refs.feedbackDialog.open) this.$refs.feedbackDialog.close();
    this.$refs.feedbackDialog.showModal();
  },
  handleError(error) {
    const resolved = error instanceof Error ? error : new Error(String(error));
    this.globalError = resolved.message || "发生未知错误";
    this.errorCode = resolved.code || "UNEXPECTED_ERROR";
    if (resolved instanceof ApiError && resolved.isConflict) this.conflictMessage = "数据已被其他页面修改，请刷新当前数据后重试。";
    if (resolved.diagnostics?.length) {
      this.preview = this.preview ? { ...this.preview, diagnostics: resolved.diagnostics } : this.preview;
    }
  },
  clearError() { this.globalError = ""; this.errorCode = ""; },
  markDirty() { this.dirty = true; },
  markSaved() { this.dirty = false; },
  async refreshSelected() {
    this.conflictMessage = "";
    if (this.activeView === "audits" && this.selectedAudit) await this.loadAudit(this.selectedAudit.id);
    else if (this.activeView === "settings") await this.loadSettings();
    else await this.loadProjects();
    this.markSaved();
  },

  async loadProjects() { await this.runAction("projects", async () => { this.projects = await apiRequest("/api/project-versions"); }); },
  async selectVersion(versionId, view = "audits") {
    if (this.dirty && !window.confirm("当前有未保存修改，仍要切换项目版本吗？")) return;
    this.selectedVersionId = versionId; this.activeView = view; this.selectedAudit = null; this.exportJob = null; this.markSaved();
    await Promise.all([this.loadAudits(), this.loadSettings()]);
  },

  async uploadPreview() {
    if (!this.selectedFile) return;
    await this.runAction("import", async () => {
      const form = new FormData(); form.append("file", this.selectedFile);
      const payload = await apiRequest("/api/imports/preview", { method: "POST", body: form, timeout: 60000 });
      this.preview = payload.preview; this.previewToken = payload.preview_token;
      this.phaseOverrides = Object.fromEntries([...(payload.preview.phases || []), ...(payload.preview.excluded_top_level || [])].map((phase) => [phase.wbs, (payload.preview.phases || []).some((value) => value.wbs === phase.wbs)]));
      this.checklistMappings = Object.fromEntries(this.mappingSubjects.map((subject) => [subject, ""]));
      this.markDirty();
    });
  },
  async confirmPreview() {
    await this.runAction("import", async () => {
      const mappings = Object.fromEntries(Object.entries(this.checklistMappings).filter(([, value]) => value));
      const payload = await apiRequest(`/api/imports/preview/${encodeURIComponent(this.previewToken)}/confirm`, { method: "POST", body: JSON.stringify({ phase_overrides: this.phaseOverrides, checklist_mappings: mappings, save_global_mappings: this.saveGlobalMappings, config_item_number: this.importConfigNumber }), timeout: 60000 });
      this.selectedVersionId = payload.project_version_id; this.preview = null; this.previewToken = ""; this.markSaved();
      await this.loadProjects(); await this.selectVersion(payload.project_version_id, "audits");
    });
  },

  async loadAudits() {
    if (!this.selectedVersionId) { this.audits = []; return; }
    await this.runAction("audits", async () => { this.audits = await apiRequest(`/api/project-versions/${this.selectedVersionId}/audits`); });
  },
  async loadAudit(auditId) {
    if (this.dirty && this.selectedAudit?.id !== auditId && !window.confirm("当前有未保存修改，仍要切换审核吗？")) return;
    await this.runAction("audit-detail", async () => {
      this.selectedAudit = await apiRequest(`/api/audits/${auditId}`);
      for (const group of this.selectedAudit.groups) for (const item of group.items) {
        item.selectedCandidateIds = [];
        item.showCustomIssue = false;
        item.customIssue = {
          title: "", description: "", severity: item.starred_snapshot ? "严重" : "一般",
          responsible_person: this.selectedAudit.project_leader || "",
          submitted_date: this.selectedAudit.audit_date || "", corrective_action: "",
        };
      }
      this.markSaved();
    });
  },
  async reloadAuditAndList() { const id = this.selectedAudit?.id; await this.loadAudits(); if (id) await this.loadAudit(id); },
  async saveAudit() {
    await this.runAction("audit", async () => {
      this.selectedAudit = await apiRequest(`/api/audits/${this.selectedAudit.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: this.selectedAudit.revision, auditor: this.selectedAudit.auditor, confirmation_state: this.selectedAudit.confirmation_state }) });
      this.markSaved(); await this.loadAudits();
    });
  },
  async saveConclusion(item) {
    await this.runAction(`item-${item.id}`, async () => {
      if (!item.conclusion) throw new Error("请选择检查结论");
      if (item.conclusion === "不符合" && !item.issues.length && !item.selectedCandidateIds.length) throw new Error("不符合结论必须至少选择一个问题模板，或先创建自定义问题");
      const candidateIds = item.selectedCandidateIds.map(Number);
      const result = await apiRequest(`/api/checklist-items/${item.id}/conclusion`, { method: "PUT", body: JSON.stringify({ expected_revision: this.selectedAudit.revision, conclusion: item.conclusion, issue_template_ids: candidateIds, clear_issues: item.conclusion !== "不符合" }) });
      this.markSaved(); await this.loadAudit(result.audit_id); await this.loadAudits();
    });
  },
  async addCustomIssue(item) {
    const issue = item.customIssue;
    if (!issue.title.trim() || !issue.description.trim()) throw new Error("自定义问题必须填写标题和描述");
    await this.runAction(`item-${item.id}`, async () => {
      const result = await apiRequest(`/api/checklist-items/${item.id}/issues`, {
        method: "POST",
        body: JSON.stringify({
          expected_revision: this.selectedAudit.revision,
          title: issue.title, description: issue.description,
          severity: item.starred_snapshot ? "严重" : issue.severity,
          responsible_person: issue.responsible_person || null,
          submitted_date: issue.submitted_date || null,
          corrective_action: issue.corrective_action || null,
        }),
      });
      this.markSaved(); await this.loadAudit(result.audit_id); await this.loadAudits();
    });
  },
  async saveIssue(issue) {
    await this.runAction(`issue-${issue.id}`, async () => {
      const fields = ["title", "description", "severity", "responsible_person", "submitted_date", "planned_close_date", "corrective_action", "actual_close_date", "verification_date", "verification_conclusion", "suggested_closed", "confirmed"];
      const body = { expected_revision: this.selectedAudit.revision };
      for (const field of fields) body[field] = issue[field] === "" ? null : issue[field];
      const result = await apiRequest(`/api/issues/${issue.id}`, { method: "PATCH", body: JSON.stringify(body) });
      this.markSaved();
      this.showFeedback("success", "保存成功", "问题信息已保存。");
      await this.loadAudit(result.audit_id); await this.loadAudits();
    }, (error) => {
      const resolved = error instanceof Error ? error : new Error(String(error));
      this.showFeedback("error", "保存失败", resolved.message || "发生未知错误，请检查后重试。");
    });
  },
  findIssueItem(issueId) { return this.selectedAudit.groups.flatMap((group) => group.items).find((item) => item.issues.some((issue) => issue.id === issueId)); },
  async removeIssue(issue) {
    if (!window.confirm(`删除问题“${issue.title}”？`)) return;
    const item = this.findIssueItem(issue.id);
    await this.runAction(`issue-${issue.id}`, async () => {
      const result = await apiRequest(`/api/issues/${issue.id}?expected_revision=${this.selectedAudit.revision}&set_conclusion_to_compliant=${item?.issues.length === 1}`, { method: "DELETE" });
      this.markSaved(); await this.loadAudit(result.audit_id); await this.loadAudits();
    });
  },
  async saveIssueAsTemplate(issue, item) {
    if (!window.confirm(`将问题“${issue.title}”整理后保存到全局问题模板库？`)) return;
    const title = window.prompt("可复用的问题标题", issue.title);
    const description = title && window.prompt("可复用的问题描述", issue.description);
    const corrective_suggestion = description && window.prompt("整改建议（不是实际整改措施）", "");
    if (!title || !description || !corrective_suggestion) return;
    const default_severity = item.starred_snapshot ? "严重" : (window.prompt("严重程度：轻微或一般", issue.severity === "严重" ? "一般" : issue.severity) || "");
    if (!default_severity) return;
    await this.runAction(`issue-template-${issue.id}`, async () => {
      await apiRequest(`/api/issues/${issue.id}/save-as-template`, {
        method: "POST",
        body: JSON.stringify({ title, description, corrective_suggestion, default_severity }),
      });
      await this.loadTemplates();
    });
  },

  async loadSettings() {
    if (!this.selectedVersionId) { this.settings = null; return; }
    await this.runAction("settings-load", async () => { this.settings = await apiRequest(`/api/project-versions/${this.selectedVersionId}/settings`); });
  },
  async saveSettings() {
    await this.runAction("settings", async () => {
      this.settings = await apiRequest(`/api/project-versions/${this.selectedVersionId}/settings`, { method: "PATCH", body: JSON.stringify({ expected_revision: this.settings.revision, config_item_number: this.settings.config_item_number, trend_conclusion_override: this.settings.trend_conclusion_override || null }) });
      this.markSaved();
    });
  },

  async startExport() {
    await this.runAction("export", async () => {
      const key = window.crypto?.randomUUID?.() || `qualitym-${Date.now()}-${Math.random()}`;
      this.exportJob = await apiRequest(`/api/project-versions/${this.selectedVersionId}/exports`, { method: "POST", headers: { "Idempotency-Key": key }, timeout: 60000 });
      this.scheduleExportPoll();
    });
  },
  scheduleExportPoll() { window.clearTimeout(this.exportTimer); if (this.exportIsActive) this.exportTimer = window.setTimeout(() => this.pollExport(), 700); },
  async pollExport() {
    try { this.exportJob = await apiRequest(`/api/exports/${this.exportJob.id}`, { timeout: 15000 }); this.scheduleExportPoll(); }
    catch (error) { this.handleError(error); this.exportTimer = window.setTimeout(() => this.pollExport(), 2000); }
  },
  async retryExport() { await this.runAction("export", async () => { this.exportJob = await apiRequest(`/api/exports/${this.exportJob.id}/retry`, { method: "POST" }); this.scheduleExportPoll(); }); },
  formatBytes(value) { if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB`; },

  async previewDelete(project) {
    await this.runAction("delete-preview", async () => {
      this.deleteVersionId = project.project_version_id; this.deleteConfirmation = "";
      this.deletePreview = await apiRequest(`/api/project-versions/${project.project_version_id}/delete-preview`, { method: "POST" });
      this.$refs.deleteDialog.showModal();
    });
  },
  async confirmDelete() {
    await this.runAction("delete", async () => {
      await apiRequest(`/api/project-versions/${this.deleteVersionId}/delete-confirm`, { method: "POST", body: JSON.stringify({ preview_token: this.deletePreview.preview_token, confirmation_text: this.deleteConfirmation }) });
      this.$refs.deleteDialog.close();
      if (this.selectedVersionId === this.deleteVersionId) { this.selectedVersionId = null; this.selectedAudit = null; this.settings = null; }
      this.deletePreview = null; this.deleteVersionId = null; await this.loadProjects();
    });
  },

  async loadTemplates() { await this.runAction("templates-load", async () => { this.templates = await apiRequest("/api/templates/checklists"); }); },
  async templateMutation(action) { await this.runAction("templates", async () => { await action(); await this.loadTemplates(); }); },
  async createChecklist() { const stable_key = window.prompt("检查单稳定键（小写英文、数字、点或连字符）"); const name = stable_key && window.prompt("检查单名称"); const audit_kind = name && window.prompt("审核类型：product 或 process", "product"); if (stable_key && name && audit_kind) await this.templateMutation(() => apiRequest("/api/templates/checklists", { method: "POST", body: JSON.stringify({ stable_key, name, audit_kind, display_order: this.templates.length + 1 }) })); },
  async createGroup(checklist) { const stable_key = window.prompt("分组稳定键"); const name = stable_key && window.prompt("分组名称"); if (stable_key && name) await this.templateMutation(() => apiRequest("/api/templates/groups", { method: "POST", body: JSON.stringify({ parent_id: checklist.id, stable_key, name, display_order: checklist.groups.length + 1 }) })); },
  async createItem(group) { const stable_key = window.prompt("检查项稳定键"); const display_no = stable_key && window.prompt("显示序号", String(group.items.length + 1)); const content = display_no && window.prompt("检查项内容"); if (stable_key && display_no && content) await this.templateMutation(() => apiRequest("/api/templates/items", { method: "POST", body: JSON.stringify({ parent_id: group.id, stable_key, display_no, content, starred: false, display_order: group.items.length + 1 }) })); },
  async createIssue(item) { const stable_key = window.prompt("问题稳定键"); const title = stable_key && window.prompt("问题标题"); const description = title && window.prompt("问题描述"); const corrective_suggestion = description && window.prompt("整改建议"); const default_severity = corrective_suggestion && (item.starred ? "严重" : window.prompt("严重程度：轻微或一般", "一般")); if (stable_key && title && description && corrective_suggestion && default_severity) await this.templateMutation(() => apiRequest("/api/templates/issues", { method: "POST", body: JSON.stringify({ item_id: item.id, stable_key, title, description, corrective_suggestion, default_severity, placeholders: [], display_order: item.issues.length + 1 }) })); },
  async editEntity(type, entity) { let body; if (type === "checklists" || type === "groups") { const name = window.prompt("名称", entity.name); if (!name) return; body = { name }; } else { const display_no = window.prompt("显示序号", entity.display_no); const content = display_no && window.prompt("检查项内容", entity.content); if (!display_no || !content) return; body = { display_no, content }; } await this.patchTemplate(type, entity.id, body); },
  async editIssue(issue, item) { const title = window.prompt("问题标题", issue.title); const description = title && window.prompt("问题描述", issue.description); const corrective_suggestion = description && window.prompt("整改建议", issue.corrective_suggestion); const default_severity = corrective_suggestion && (item.starred ? "严重" : window.prompt("严重程度：轻微或一般", issue.default_severity)); if (title && description && corrective_suggestion && default_severity) await this.patchTemplate("issues", issue.id, { title, description, corrective_suggestion, default_severity }); },
  async patchTemplate(type, id, body) { await this.templateMutation(() => apiRequest(`/api/templates/${type}/${id}`, { method: "PATCH", body: JSON.stringify(body) })); },
  async toggleEntity(type, entity) { await this.patchTemplate(type, entity.id, { enabled: !entity.enabled }); },
  async copyIssue(issue) { const stable_key = window.prompt("副本稳定键", `${issue.stable_key}.copy`); if (stable_key) await this.templateMutation(() => apiRequest(`/api/templates/issues/${issue.id}/copy`, { method: "POST", body: JSON.stringify({ stable_key }) })); },
  async downloadTemplates() { await this.runAction("templates", async () => saveBlob(await apiRequest("/api/templates/export", { method: "POST", timeout: 60000 }), "QualityM-模板库.xlsx")); },
  async previewTemplateImport() { if (!this.templateFile) return; await this.runAction("templates", async () => { const form = new FormData(); form.append("file", this.templateFile); const payload = await apiRequest("/api/templates/import-preview", { method: "POST", body: form, timeout: 60000 }); this.templateDiff = payload.changes; this.templatePreviewToken = payload.preview_token; }); },
  async commitTemplateImport() { await this.templateMutation(async () => { await apiRequest("/api/templates/import-commit", { method: "POST", body: JSON.stringify({ preview_token: this.templatePreviewToken }) }); this.templateDiff = null; this.templatePreviewToken = ""; }); },
});

const alpineScript = document.createElement("script");
alpineScript.src = "https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js";
alpineScript.defer = true;
alpineScript.onerror = () => { document.body.innerHTML = '<main class="card"><h1>QualityM 前端资源加载失败</h1><p>请检查网络连接后刷新页面。</p></main>'; };
document.head.appendChild(alpineScript);
