// Copyright (C) 2026 Sacrappy

/* (PIPE2, stage 0) the frame's two stages' slack: hos_slack.c */
#pragma once

#include <stdint.h>

enum { HOS_SLACK_QUEUE, HOS_SLACK_EVENT };
/* a guest wait that blocked, of that kind, called from lr, ns long (nn_os_sync.c) */
void hos_slack_wait(int kind, uint64_t lr, int64_t ns);
/* the presenting thread presents (nvn_native.c): every 600 the log line */
void hos_slack_present(void *thread);
