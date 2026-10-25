import assert from "node:assert/strict";
import test from "node:test";
import { formatActionReports } from "./actionReports.js";

test("compiler facts identify actual file line without claiming runtime correctness", () => {
    const failed = { checked: true, syntax_valid: false, source_sha256: "before",
        diagnostic: { type: "SyntaxError", message: "expected ':'", line: 2, offset: 20 } };
    const passed = { checked: true, syntax_valid: true, source_sha256: "saved" };
    const report = formatActionReports([
        { tool: "file_read", ok: true, python_syntax_check: failed },
        { tool: "file_write", ok: true, change_evidence: {
            before_sha256: "before", after_sha256: "saved", diff_available: true, unified_diff: ":",
            python_syntax_check: { before: failed, saved: passed },
        } },
        { tool: "file_read", ok: true, python_syntax_check: {
            checked: false, source_sha256: "large", error: "source too large",
        } },
    ]);
    assert.match(report, /SyntaxError at file line 2, offset 20: expected ':'/);
    assert.match(report, /Saved Python syntax: PASSED \(syntax only\)/);
    assert.match(report, /NOT CHECKED: source too large/);
    assert.match(report, /Syntax success is not runtime or output proof/);
    assert.doesNotMatch(report, /SAVED FILE RUN PASSED/);
});

test("yes all three please: verified write cannot hide failed run or invent jellybeans", () => {
    const report = formatActionReports([
        {
            tool: "file_write", ok: true, target: "E:\\workspace\\sparkly_fireworks.py",
            verification: { exists: true, content_matches: true, sha256: "abc123", first_line: "import random" },
        },
        {
            tool: "code_execute", ok: false, exit_code: 1,
            error: "process_exited_with_code_1", stderr: "subprocess.TimeoutExpired: timed out after 3 seconds",
        },
    ]);
    assert.match(report, /WRITE VERIFIED/);
    assert.match(report, /SHA-256: abc123/);
    assert.match(report, /code_execute: FAILED/);
    assert.match(report, /Exit code: 1/);
    assert.match(report, /timed out after 3 seconds/);
    assert.doesNotMatch(report, /jellybean|mode activated|script is running/i);
});

test("no execution is explicit, unknown transport is not invented", () => {
    assert.match(formatActionReports([]), /No tools executed/);
    assert.equal(formatActionReports(undefined), null);
});

test("successful wrapper reports stderr without claiming background liveness", () => {
    const report = formatActionReports([
        { tool: "code_execute", ok: true, exit_code: 0, stderr: "child failed" },
    ]);
    assert.match(report, /COMMAND FINISHED/);
    assert.match(report, /child failed/);
    assert.match(report, /does not prove an application is still running/);
});

test("source, syntax and saved-file run remain distinct proof levels", () => {
    for (const [mode, label] of [
        ["inspect", "SOURCE INSPECTED"],
        ["check", "SYNTAX CHECK PASSED"],
        ["run", "SAVED FILE RUN PASSED"],
    ]) {
        const report = formatActionReports([{
            tool: "code_execute", ok: true, mode, sha256: "tested123",
            exit_code: mode === "inspect" ? null : 0, target: "E:\\workspace\\repair.py",
        }]);
        assert.ok(report.includes(label));
        assert.match(report, /Tested\/source SHA-256: tested123/);
        assert.doesNotMatch(report, /Exit code: null/);
        assert.match(report, /does not prove an application is still running/);
    }
});

test("exit zero with wrong output is still a failed test", () => {
    const report = formatActionReports([{
        tool: "code_execute", ok: false, mode: "run", exit_code: 0,
        error: "stdout_mismatch", expected_stdout: "jellybean sparkle\n",
        stdout: "jellybean sparkle\n\n",
    }]);
    assert.match(report, /FAILED/);
    assert.match(report, /stdout_mismatch/);
    assert.ok(report.includes('Expected stdout (exact): "jellybean sparkle\\n"'));
    assert.doesNotMatch(report, /SAVED FILE RUN PASSED/);
});

test("a successful idempotent save is not described as a code change", () => {
    const report = formatActionReports([{
        tool: "file_write", ok: true, changed: false,
        verification: { exists: true, content_matches: true, sha256: "unchanged" },
    }]);
    assert.match(report, /WRITE VERIFIED/);
    assert.match(report, /File unchanged: no code change was applied/);
});

test("coding specialist consultation is not write or execution proof", () => {
    const report = formatActionReports([{
        tool: "coding_specialist", ok: true, model: "qwen2.5-coder-7b-instruct",
        saved: false, tested: false,
    }]);
    assert.match(report, /SPECIALIST CONSULTED \(NOT SAVED OR TESTED\)/);
    assert.match(report, /qwen2.5-coder-7b-instruct/);
    assert.doesNotMatch(report, /WRITE VERIFIED|SAVED FILE RUN PASSED/);
});

test("healthy source assessment is visible without pretending it was tested", () => {
    const report = formatActionReports([{
        tool: "coding_specialist", ok: true, status: "no_change",
        summary: "Already computes the sum and prints one newline.",
        assessment_verified: false, saved: false, tested: false, changed: false,
    }]);
    assert.match(report, /Specialist assessment \(unverified\): no_change/);
    assert.match(report, /Specialist says: Already computes the sum/);
    assert.doesNotMatch(report, /WRITE VERIFIED|SAVED FILE RUN PASSED/);
});

test("repair workflow retains save and failed-test receipts", () => {
    const report = formatActionReports([{
        tool: "code_repair", ok: false, verification_level: "expected_output",
        error: "code_repair_verification_failed_after_two_attempts",
        steps: [
            { tool: "file_write", ok: true,
                verification: { exists: true, content_matches: true, sha256: "saved" } },
            { tool: "code_execute", ok: false, mode: "run", exit_code: 1, stderr: "NameError" },
        ],
    }]);
    assert.match(report, /code_repair: FAILED/);
    assert.match(report, /WRITE VERIFIED/);
    assert.match(report, /code_execute: FAILED/);
    assert.match(report, /NameError/);
    assert.doesNotMatch(report, /REPAIR TEST PASSED|SAVED FILE RUN PASSED/);
});

test("change evidence and actual BOM facts stay separate from interpretation", () => {
    const report = formatActionReports([
        { tool: "code_repair", ok: true, changes: [{
            before_sha256: "before", after_sha256: "saved", diff_available: true,
            unified_diff: "-for value in values\n+for value in values:\n", truncated: true,
        }] },
        { tool: "file_read", ok: true, decoder_used: "utf-8", bom: null },
    ]);
    assert.match(report, /Observed text diff \(not diagnosis\)/);
    assert.match(report, /\+for value in values:/);
    assert.match(report, /Text diff truncated/);
    assert.match(report, /Decoder used: utf-8/);
    assert.match(report, /BOM bytes: none/);
    assert.doesNotMatch(report, /indentation/);
});
