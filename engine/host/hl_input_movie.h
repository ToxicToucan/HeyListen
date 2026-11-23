/* (fork) Input recordings for identical A/B runs: host/hl_input_movie.c (after Longshot 971f77f52) */
#pragma once
#include <stdint.h>

typedef struct {
    uint64_t bits;      /* (the NpadButton word the game is given) */
    int32_t stick[4];   /* (lx, ly, rx, ry) */
    uint64_t flight;    /* (the flight cheat's packed stick and climb, nn_hid.c flight_input; 0 without it) */
    uint64_t axes;      /* (the free camera's or first person's packed sticks, free_camera_axes) */
    int32_t height;     /* (the free camera's height: R up, L down) */
} MoviePad;

int hos_input_movie_active(void);
int hos_input_movie_begin(MoviePad *given);
void hos_input_movie_end(const MoviePad *p);
void hos_input_movie_link(const float pos[3]);
