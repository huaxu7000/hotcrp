<?php
// Create synthetic local test data; refuses to run on another database.
require_once dirname(__DIR__) . "/src/init.php";
$conf = initialize_conf();
if ($conf->dbname !== "hotcrp_local" || $conf->opt("paperSite") !== "http://127.0.0.1:8080") {
    throw new Exception("This seed is only for the local test conference");
}
if ((int) $conf->fetch_ivalue("select count(*) from ContactInfo") !== 0) {
    throw new Exception("Seed requires a fresh database; use the saved baseline to reset");
}
$accounts = [];
foreach ([
    ["admin@hotcrp.localhost", "Local Administrator", ["chair", "pc"]],
    ["author@hotcrp.localhost", "Test Author", []],
    ["reviewer@hotcrp.localhost", "Test Reviewer", ["pc"]],
    ["outsider@hotcrp.localhost", "Unassigned Test User", []]
] as [$email, $name, $roles]) {
    $us = new UserStatus($conf->root_user());
    $us->set_notify(false);
    $u = $us->save_user((object) ["email" => $email, "name" => $name, "roles" => $roles]);
    if (!$u) { throw new Exception($us->full_feedback_text()); }
    $password = bin2hex(random_bytes(10));
    $u->change_password($password);
    $accounts[$email] = $password;
}
$credentials = dirname(__DIR__) . "/var/local/accounts.json";
file_put_contents($credentials, json_encode($accounts, JSON_PRETTY_PRINT) . "\n");
chmod($credentials, 0600);
$conf->save_setting("setupPhase", null);
$author = $conf->user_by_email("author@hotcrp.localhost");
$ps = (new PaperStatus($author))->set_any_content_file(true);
$pj = (object) [
    "title" => "Local HotCRP workflow test",
    "abstract" => "Synthetic submission for testing uploads, assignments, reviews, and visibility.",
    "authors" => [(object) ["email" => $author->email, "first" => "Test", "last" => "Author", "affiliation" => "Local Testing", "contact" => true]],
    "submission" => (object) ["content_file" => dirname(__DIR__) . "/test/sample-linearized.pdf", "filename" => "sample-paper.pdf", "mimetype" => "application/pdf"],
    "submitted" => true
];
if (!$ps->save_paper_json($pj)) { throw new Exception($ps->full_feedback_text()); }
$pid = $ps->saved_pid();
$aset = new AssignmentSet($conf->root_user());
$aset->parse("paper,action,email\n{$pid},primary,reviewer@hotcrp.localhost\n");
if (!$aset->execute()) { throw new Exception($aset->full_feedback_text()); }
$reviewer = $conf->user_by_email("reviewer@hotcrp.localhost");
$prow = $conf->paper_by_id($pid, $reviewer);
$rv = (new ReviewValues($reviewer))->set_notify(false);
$rv->parse_json((object) ["pid" => $pid, "ready" => true, "Overall merit" => 4, "Reviewer expertise" => 3, "Paper summary" => "The local test submission is readable and the review workflow works.", "Comments for authors" => "Synthetic review for local testing.", "Comments for PC" => "Local test: no real conference data."]);
if (!$rv->check_and_save($prow)) { throw new Exception($rv->full_feedback_text()); }
$prow = $conf->paper_by_id($pid, $reviewer);
$rrow = $prow->fresh_review_by_user($reviewer);
$outsider = $conf->user_by_email("outsider@hotcrp.localhost");
foreach ([
    "author sees own paper" => $author->can_view_paper($prow),
    "reviewer sees assigned paper" => $reviewer->can_view_paper($prow),
    "review saved as submitted" => $rrow && $rrow->reviewSubmitted > 0,
    "author cannot see unreleased review" => !$author->can_view_review($prow, $rrow),
    "outsider cannot see paper" => !$outsider->can_view_paper($prow),
    "reviewer cannot see author identity" => !$reviewer->can_view_authors($prow)
] as $label => $ok) {
    echo ($ok ? "PASS" : "FAIL") . ": {$label}\n";
    if (!$ok) { throw new Exception($label); }
}
echo "Created sample paper #{$pid}; passwords saved only in var/local/accounts.json\n";
