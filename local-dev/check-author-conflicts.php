<?php
// Read-only regression checks: prepare saves, but never execute them.
require_once dirname(__DIR__) . "/src/init.php";
$conf = initialize_conf();
function check_conflict_policy($ok, $label) {
    if (!$ok) {
        throw new Exception($label);
    }
    echo "PASS: {$label}\n";
}
$author = $conf->user_by_email("author@hotcrp.localhost");
$chair = $conf->user_by_email("admin@hotcrp.localhost");
$prow = $conf->paper_by_id(1, $chair);
$opt = $conf->option_by_id(PaperOption::PCCONFID);
foreach ([$prow, PaperInfo::make_new($author, "")] as $p) {
    check_conflict_policy(!$author->can_view_option($p, $opt), "PC conflicts hidden from author on " . ($p->paperId ? "existing" : "new") . " submission");
    check_conflict_policy(!$author->can_edit_option($p, $opt), "Author cannot edit PC conflicts");
}
check_conflict_policy($chair->can_view_option($prow, $opt), "Chair can view PC conflicts");
check_conflict_policy($chair->can_edit_option($prow, $opt), "Chair can edit PC conflicts");
$prow = $conf->paper_by_id(1, $chair);
$ps = new PaperStatus($author);
check_conflict_policy($ps->prepare_save_paper_json((object) ["pid" => 1, "abstract" => $prow->abstract], $prow), "Author save without PC conflicts passes validation");
$prow = $conf->paper_by_id(1, $chair);
$ps = (new PaperStatus($author))->set_ignore_unwritable_fields(false);
$ps->prepare_save_paper_json((object) ["pid" => 1, "pc_conflicts" => (object) ["reviewer@hotcrp.localhost" => true]], $prow);
check_conflict_policy(!in_array("pc_conflicts", $ps->change_list(), true), "Author-supplied JSON cannot change PC conflicts");
$prow = $conf->paper_by_id(1, $chair);
$ps = new PaperStatus($chair);
check_conflict_policy($ps->prepare_save_paper_json((object) ["pid" => 1, "pc_conflicts" => (object) ["reviewer@hotcrp.localhost" => true]], $prow), "Chair conflict update passes validation");

check_conflict_policy(in_array("pc_conflicts", $ps->change_list(), true), "Chair conflict update is staged for saving");

$conf->set_opt("collectAuthorPCConflicts", true);
$prow = $conf->paper_by_id(1, $chair);
check_conflict_policy($author->can_view_option($prow, $opt) && $author->can_edit_option($prow, $opt), "Original author behavior retained when collection is enabled");
