/*
 * Transcribr - app bundle entry point.
 *
 * A compiled launcher rather than a shell script, because the hardened
 * runtime that notarisation requires can only be applied to a Mach-O
 * binary. All it does is locate the bundled interpreter and hand over
 * to bootstrap.py, which does the real work.
 *
 * Built by macos/build-pkg.sh:
 *     clang -O2 -Wall -o Transcribr launcher.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[])
{
    char exe[PATH_MAX];
    uint32_t size = sizeof(exe);

    if (_NSGetExecutablePath(exe, &size) != 0) {
        fprintf(stderr, "Transcribr: could not locate the app bundle.\n");
        return 1;
    }

    /* exe is .../Transcribr.app/Contents/MacOS/Transcribr - climb to
     * Contents/ by trimming the last two path components. */
    char *slash = strrchr(exe, '/');
    if (slash) *slash = '\0';               /* .../Contents/MacOS      */
    slash = strrchr(exe, '/');
    if (slash) *slash = '\0';               /* .../Contents            */

    char python[PATH_MAX], bootstrap[PATH_MAX];
    snprintf(python, sizeof(python),
             "%s/Resources/python/bin/python3", exe);
    snprintf(bootstrap, sizeof(bootstrap),
             "%s/Resources/bootstrap.py", exe);

    if (access(python, X_OK) != 0) {
        fprintf(stderr, "Transcribr: bundled runtime missing at %s\n", python);
        return 1;
    }

    /* python3 bootstrap.py <original args...> */
    char **args = calloc((size_t)argc + 3, sizeof(char *));
    if (!args) return 1;
    args[0] = python;
    args[1] = bootstrap;
    for (int i = 1; i < argc; i++)
        args[i + 1] = argv[i];
    args[argc + 1] = NULL;

    execv(python, args);

    /* Only reached if execv failed. */
    perror("Transcribr: could not start the bundled interpreter");
    return 1;
}
