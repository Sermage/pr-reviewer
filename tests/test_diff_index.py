from app.diff_index import commentable_lines

DIFF = """\
diff --git a/app/Main.kt b/app/Main.kt
index 1111..2222 100644
--- a/app/Main.kt
+++ b/app/Main.kt
@@ -10,3 +10,5 @@ class Main {
     val a = 1
+    val b = 2
+    val c = 3
     val d = 4
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-old title
+new title
 body
"""


def test_added_and_context_lines_are_commentable():
    idx = commentable_lines(DIFF)
    # hunk starts at new-file line 10: context(10), added(11), added(12), context(13)
    assert idx["app/Main.kt"] == {10, 11, 12, 13}


def test_second_file_tracked_independently():
    idx = commentable_lines(DIFF)
    # line 1 = added "new title", line 2 = context "body"; removed line not counted
    assert idx["README.md"] == {1, 2}


def test_deleted_file_has_no_anchors():
    diff = "diff --git a/x b/x\n--- a/x\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
    assert commentable_lines(diff) == {}
