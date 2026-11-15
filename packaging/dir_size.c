/* Logical sizes of regular files. No symlink traversal or hardlink deduplication. */
#include <sys/stat.h>
#include <errno.h>
#include <fts.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    struct stat root;
    if (lstat(argv[1], &root) != 0 || !S_ISDIR(root.st_mode)) return 2;
    char *paths[] = {argv[1], NULL};
    FTS *tree = fts_open(paths, FTS_PHYSICAL | FTS_NOCHDIR, NULL);
    if (!tree) return 2;
    uint64_t total = 0;
    int failed = 0;
    double last = now();
    for (;;) {
        errno = 0;
        FTSENT *entry = fts_read(tree);
        if (!entry) { if (errno) failed = 1; break; }
        if (entry->fts_info == FTS_F) {
            off_t size = entry->fts_statp->st_size;
            if (size < 0 || UINT64_MAX - total < (uint64_t)size) { failed = 1; break; }
            total += (uint64_t)size;
        } else if (entry->fts_info == FTS_ERR || entry->fts_info == FTS_DNR ||
                   entry->fts_info == FTS_NS || entry->fts_info == FTS_DC) {
            failed = 1;
        }
        if (now() - last >= 0.4) {
            printf("{\"bytes\":%" PRIu64 ",\"complete\":false}\n", total);
            fflush(stdout);
            last = now();
        }
    }
    if (fts_close(tree) != 0) failed = 1;
    if (failed) return 1;
    printf("{\"bytes\":%" PRIu64 ",\"complete\":true}\n", total);
    return 0;
}
