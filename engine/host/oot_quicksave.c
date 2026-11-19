/* (fork) Quick save and quick load, the files' side (host/hos_main.c has the keys and the place).

   A quick save (F5 in play) is a folder of its own, <game>\quicksave\<YYYYMMDD-HHMMSS>: the save data as the game last
   wrote it (user\save, without its uncommitted *.work.* copies), with the newest slot's progress.sav brought up to this
   moment, and position.txt (the map, then Link's x y z). quicksave\latest.txt names the newest; the three newest are kept
   (never the one this game runs from).

   "This moment": progress.sav (no checksum) is a header (magic 0x01020304, version, the offset of the data area), then
   from 0x20 (hash, value) pairs in typed sections, each opened by a (0, section) pair. Sections 0 (bools), 2 (ints),
   4 (floats) and 6 (enums) hold their values inline; the others point into the data area (strings, arrays, vectors) and
   stay as last saved. The live values are in GameDataMgr's type entries: {vtable, ?, count u32 @16, objects @24,
   (hash, index) table @32, table size u32 @44}, the objects a fixed size apart with the value at a fixed offset.

   It is not a snapshot: a save never holds enemies, physics or a running scene, so those start over when it is loaded;
   the area, Link's place, and the items and flags of the moment come back.

   From the OoT recomp's quick save (its "save the moment" and problem reports), which follows sloperina's FINDINGS 1 and
   gamedata.c (GPL-3.0) for the save's and GameDataMgr's layouts. */
#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#ifdef _WIN32
typedef struct { uint64_t objects, table; uint32_t count, size; } LiveType;

static int live_type_at(Ctx *c, uint64_t entry, LiveType *t) {
    t->count = recomp_load32(c, entry + 16), t->objects = recomp_load64(c, entry + 24);
    t->table = recomp_load64(c, entry + 32), t->size = recomp_load32(c, entry + 44);
    return t->count && t->count < 0x10000 && t->size && t->size < 0x100000 && t->objects >= HOS_HEAP_BASE &&
           t->table >= HOS_HEAP_BASE;
}

static int live_index(Ctx *c, const LiveType *t, uint32_t hash) {
    for (uint32_t probe = 0, slot = hash % t->size; probe < t->size; probe++, slot = (slot + 1) % t->size) {
        const uint32_t key = recomp_load32(c, t->table + slot * 8ULL);
        if (!key) return -1;
        if (key == hash) {
            const uint32_t i = recomp_load32(c, t->table + slot * 8ULL + 4);
            return i < t->count ? (int)i : -1;
        }
    }
    return -1;
}

/* progress.sav at path brought up to the live values of GameDataMgr (manager); a line of what changed in out. 0: left as
   it was (not the layout this expects, or nothing found) */
int hos_qs_save_now(Ctx *c, uint64_t manager, const char *path, char *out, size_t out_size) {
    out[0] = 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    const long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = size > 0x28 ? (uint8_t *)malloc((size_t)size) : NULL;
    const int read_ok = b && fread(b, 1, (size_t)size, f) == (size_t)size;
    fclose(f);
    uint32_t magic = 0, data_at = 0;
    if (read_ok) memcpy(&magic, b, 4), memcpy(&data_at, b + 8, 4);
    if (!read_ok || magic != 0x01020304u || data_at > (uint32_t)size || !manager) {
        free(b);
        return 0;
    }
    static const struct { int section; uint32_t stride, value; int bytes; } kinds[] = {
        {0, 0x18, 0x16, 1}, {2, 0x28, 0x1C, 4}, {4, 0x28, 0x1C, 4}, {6, 0x38, 0x1C, 4}};
    int total = 0;
    size_t used = 0;
    for (unsigned k = 0; k < sizeof kinds / sizeof kinds[0]; k++) {
        /* the section's pairs: (offset of the value, hash) */
        size_t n = 0, cap = 0;
        uint32_t *at = NULL, *hash = NULL;
        int s = -1;
        for (uint32_t i = 0x20; i + 8 <= data_at; i += 8) {
            uint32_t h, v;
            memcpy(&h, b + i, 4), memcpy(&v, b + i + 4, 4);
            if (!h) { s = (int)v; continue; }
            if (s != kinds[k].section) continue;
            if (n == cap) {
                cap = cap ? cap * 2 : 1024;
                at = (uint32_t *)realloc(at, cap * 4), hash = (uint32_t *)realloc(hash, cap * 4);
            }
            at[n] = i + 4, hash[n] = h, n++;
        }
        /* the type entry whose table knows the most of this section's hashes (manager offsets 0 to 0x1000) */
        LiveType best = {0};
        size_t best_hits = 0;
        const size_t sample = n < 200 ? n : 200;
        for (uint64_t o = 0; o < 0x1000 && sample; o += 8) {
            LiveType t;
            if (!live_type_at(c, manager + o, &t)) continue;
            size_t hits = 0;
            for (size_t i = 0; i < sample; i++) hits += live_index(c, &t, hash[i]) >= 0;
            if (hits > best_hits) best_hits = hits, best = t;
        }
        int changed = 0, found = 0;
        if (sample && best_hits * 10 >= sample * 9) {
            for (size_t i = 0; i < n; i++) {
                const int idx = live_index(c, &best, hash[i]);
                if (idx < 0) continue;
                found++;
                const uint64_t a = best.objects + (uint64_t)idx * kinds[k].stride + kinds[k].value;
                uint32_t v = kinds[k].bytes == 1 ? (recomp_load32(c, a & ~3ULL) >> ((a & 3) * 8)) & 0xff : recomp_load32(c, a);
                if (kinds[k].bytes == 1) v = v ? 1 : 0;
                uint32_t was;
                memcpy(&was, b + at[i], 4);
                if (was != v) memcpy(b + at[i], &v, 4), changed++;
            }
            total += found;
        }
        if (used < out_size)
            used += (size_t)snprintf(out + used, out_size - used, "section %d: %d of %zu live, %d changed; ", kinds[k].section,
                                     found, n, changed);
        free(at), free(hash);
    }
    int ok = 0;
    if (total && (f = fopen(path, "wb"))) {
        ok = fwrite(b, 1, (size_t)size, f) == (size_t)size;
        fclose(f);
    }
    free(b);
    return ok;
}

/* the tree at src copied to dst, without the uncommitted *.work.* entries; how many files */
static int copy_tree(const char *src, const char *dst) {
    CreateDirectoryA(dst, NULL);
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s\\*", src);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    int files = 0;
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..") || strstr(fd.cFileName, ".work.")) continue;
        char from[MAX_PATH], to[MAX_PATH];
        snprintf(from, sizeof from, "%s\\%s", src, fd.cFileName);
        snprintf(to, sizeof to, "%s\\%s", dst, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) files += copy_tree(from, to);
        else files += CopyFileA(from, to, FALSE) != 0;
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return files;
}

/* the newest progress.sav under dir (its path in out); 0 none */
static int newest_progress(const char *dir, char *out, size_t out_size, FILETIME *newest) {
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s\\*", dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    int found = 0;
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
        char p[MAX_PATH];
        snprintf(p, sizeof p, "%s\\%s", dir, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            found |= newest_progress(p, out, out_size, newest);
        } else if (!_stricmp(fd.cFileName, "progress.sav") && CompareFileTime(&fd.ftLastWriteTime, newest) > 0) {
            *newest = fd.ftLastWriteTime;
            snprintf(out, out_size, "%s", p);
            found = 1;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return found;
}

static void delete_tree(const char *dir) {
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s\\*", dir);
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
            char p[MAX_PATH];
            snprintf(p, sizeof p, "%s\\%s", dir, fd.cFileName);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) delete_tree(p);
            else DeleteFileA(p);
        } while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryA(dir);
}

/* <game>\quicksave (made if missing) in out */
static void quick_root(char *out, size_t size) {
    char exe[MAX_PATH];
    const DWORD n = GetModuleFileNameA(NULL, exe, MAX_PATH);
    exe[n < MAX_PATH ? n : 0] = 0;
    char *slash = strrchr(exe, '\\');
    if (slash) *slash = 0;
    snprintf(out, size, "%s\\quicksave", exe);
    CreateDirectoryA(out, NULL);
}

/* A quick save of this moment: the folder's path in out. 1 when it was made */
int hos_qs_write(Ctx *c, uint64_t manager, const char *map, const float xyz[3], char *out, size_t out_size) {
    extern const char *hos_user_dir;
    char root[MAX_PATH], dir[MAX_PATH], src[MAX_PATH], save[MAX_PATH];
    quick_root(root, sizeof root);
    SYSTEMTIME t;
    GetLocalTime(&t);
    snprintf(dir, sizeof dir, "%s\\%04u%02u%02u-%02u%02u%02u", root, t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    snprintf(src, sizeof src, "%s\\save", hos_user_dir);
    snprintf(save, sizeof save, "%s\\save", dir);
    CreateDirectoryA(dir, NULL);
    const int files = copy_tree(src, save);
    if (!files) {
        hos_log("quick save: nothing to copy from %s: no quick save made", src);
        delete_tree(dir);
        return 0;
    }
    char progress[MAX_PATH] = "", what[512] = "";
    FILETIME newest = {0, 0};
    const int moment = newest_progress(save, progress, sizeof progress, &newest) &&
                       hos_qs_save_now(c, manager, progress, what, sizeof what);
    char pos[MAX_PATH];
    snprintf(pos, sizeof pos, "%s\\position.txt", dir);
    FILE *pf = fopen(pos, "w");
    if (pf) {
        fprintf(pf, "%s\n%.4f %.4f %.4f\n", map, xyz[0], xyz[1], xyz[2]);
        fclose(pf);
    }
    char latest[MAX_PATH];
    snprintf(latest, sizeof latest, "%s\\latest.txt", root);
    FILE *lf = fopen(latest, "w");
    if (lf) {
        fprintf(lf, "%s\n", dir);
        fclose(lf);
    }
    hos_log("quick save: %s (%d files; %s: %s)", dir, files, moment ? "this moment" : "as last saved", moment ? what : progress);
    /* (the three newest kept: the folders sort by their names, which are their times; never the one this game runs from) */
    char pattern[MAX_PATH];
    snprintf(pattern, sizeof pattern, "%s\\2*", root);
    char names[64][MAX_PATH];
    int count = 0;
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && count < 64) snprintf(names[count++], MAX_PATH, "%s", fd.cFileName);
        while (FindNextFileA(h, &fd));
        FindClose(h);
    }
    qsort(names, (size_t)count, MAX_PATH, (int (*)(const void *, const void *))strcmp);
    char running[MAX_PATH] = "";
    GetFullPathNameA(hos_user_dir, MAX_PATH, running, NULL);
    for (int i = 0; i + 3 < count; i++) {
        char old[MAX_PATH], full[MAX_PATH] = "";
        snprintf(old, sizeof old, "%s\\%s", root, names[i]);
        GetFullPathNameA(old, MAX_PATH, full, NULL);
        if (!_stricmp(full, running)) continue;
        delete_tree(old);
    }
    snprintf(out, out_size, "%s", dir);
    return 1;
}

/* The newest quick save's folder in dir, its map and Link's place. 1 when there is one */
int hos_qs_latest(char *dir, size_t dir_size, char *map, size_t map_size, float xyz[3]) {
    char root[MAX_PATH], latest[MAX_PATH];
    quick_root(root, sizeof root);
    snprintf(latest, sizeof latest, "%s\\latest.txt", root);
    FILE *l = fopen(latest, "r");
    if (!l) return 0;
    const int got = fgets(dir, (int)dir_size, l) != NULL;
    fclose(l);
    if (!got) return 0;
    dir[strcspn(dir, "\r\n")] = 0;
    char pos[MAX_PATH];
    snprintf(pos, sizeof pos, "%s\\position.txt", dir);
    FILE *p = fopen(pos, "r");
    if (!p) return 0;
    int ok = fgets(map, (int)map_size, p) != NULL;
    if (ok) map[strcspn(map, "\r\n")] = 0;
    ok = ok && fscanf(p, "%f %f %f", &xyz[0], &xyz[1], &xyz[2]) == 3;
    fclose(p);
    return ok;
}
#else   /* (Linux: quick saves use the Windows file calls above; none are made) */
int hos_qs_save_now(Ctx *c, uint64_t m, const char *p, char *o, size_t n) { (void)c, (void)m, (void)p; if (n) o[0] = 0; return 0; }
int hos_qs_write(Ctx *c, uint64_t m, const char *map, const float xyz[3], char *o, size_t n) {
    (void)c, (void)m, (void)map, (void)xyz; if (n) o[0] = 0;
    hos_log("quick save: made on Windows only");
    return 0;
}
int hos_qs_latest(char *d, size_t ds, char *m, size_t ms, float xyz[3]) { (void)d, (void)ds, (void)m, (void)ms, (void)xyz; return 0; }
#endif
