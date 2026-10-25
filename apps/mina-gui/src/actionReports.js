function syntaxDetails(facts, label = "Python syntax") {
    if (!facts) return [];
    const status = !facts.checked ? `NOT CHECKED: ${facts.error}`
        : facts.syntax_valid ? "PASSED (syntax only)" : "FAILED";
    const lines = [`${label}: ${status}`, `Syntax source SHA-256: ${facts.source_sha256}`];
    if (facts.diagnostic) {
        lines.push(`${facts.diagnostic.type} at file line ${facts.diagnostic.line}, offset ${facts.diagnostic.offset}: ${facts.diagnostic.message}`);
    }
    lines.push("Source was not executed by this check. Syntax success is not runtime or output proof.");
    return lines;
}

export function formatActionReports(actions) {
    if (!Array.isArray(actions)) return null;
    if (actions.length === 0) {
        return "No tools executed for this turn. Mina's reply is not action proof.";
    }
    const expanded = actions.flatMap((action) => [
        action, ...(Array.isArray(action.steps) ? action.steps : []),
    ]);
    const lines = expanded.map((action) => {
        const verifiedWrite = action.ok === true && action.verification?.exists === true
            && action.verification?.content_matches === true;
        const status = action.ok !== true ? "FAILED"
            : action.tool === "code_repair" ? "REPAIR TEST PASSED"
            : action.tool === "coding_specialist" ? "SPECIALIST CONSULTED (NOT SAVED OR TESTED)"
            : verifiedWrite ? "WRITE VERIFIED"
                : action.tool === "code_execute" && action.mode === "inspect" ? "SOURCE INSPECTED"
                    : action.tool === "code_execute" && action.mode === "check" ? "SYNTAX CHECK PASSED"
                        : action.tool === "code_execute" && action.mode === "run" ? "SAVED FILE RUN PASSED"
                            : action.exit_code !== undefined ? "COMMAND FINISHED" : "TOOL COMPLETED";
        const details = [`${action.tool}: ${status}`];
        if (action.target) details.push(`Path: ${action.target}`);
        if (action.model) details.push(`Model: ${action.model}`);
        if (action.verification_level) details.push(`Test scope: ${action.verification_level}`);
        if (action.decoder_used) details.push(`Decoder used: ${action.decoder_used}`);
        if (Object.hasOwn(action, "bom")) details.push(`BOM bytes: ${action.bom ?? "none"}`);
        details.push(...syntaxDetails(action.python_syntax_check));
        const changes = action.changes ?? (action.change_evidence ? [action.change_evidence] : []);
        for (const change of changes) {
            details.push(`Change bytes: ${change.before_sha256 ?? "(new file)"} -> ${change.after_sha256}`);
            if (change.python_syntax_check) {
                details.push(...syntaxDetails(change.python_syntax_check.before, "Before Python syntax"));
                details.push(...syntaxDetails(change.python_syntax_check.saved, "Saved Python syntax"));
            }
            if (change.diff_available) {
                details.push(`Observed text diff (not diagnosis):\n${change.unified_diff || "(no changed lines)"}`);
                if (change.truncated) details.push("[Text diff truncated]");
                if (change.before_final_newline !== change.after_final_newline) {
                    details.push(`Final newline: ${change.before_final_newline} -> ${change.after_final_newline}`);
                }
            } else {
                details.push(`Text diff unavailable: ${change.reason}`);
            }
        }
        if (action.tool === "coding_specialist" && action.status) {
            details.push(`Specialist assessment (unverified): ${action.status}`);
            if (action.summary) details.push(`Specialist says: ${action.summary}`);
        }
        if (action.exit_code != null) details.push(`Exit code: ${action.exit_code}`);
        if (action.sha256) details.push(`Tested/source SHA-256: ${action.sha256}`);
        if (action.changed === false) details.push("File unchanged: no code change was applied.");
        if (action.expected_stdout !== undefined) {
            details.push(`Expected stdout (exact): ${JSON.stringify(action.expected_stdout)}`);
        }
        if (verifiedWrite) {
            details.push(`SHA-256: ${action.verification.sha256}`);
            details.push(`First line: ${action.verification.first_line || "(empty file)"}`);
        }
        if (action.error) details.push(`Error: ${action.error}`);
        if (action.stdout) details.push(`stdout:\n${action.stdout}`);
        if (action.stderr) details.push(`stderr:\n${action.stderr}`);
        return details.join("\n");
    });
    return lines.join("\n\n")
        + "\n\nOnly these tool results are verified. A finished command does not prove an application is still running.";
}
