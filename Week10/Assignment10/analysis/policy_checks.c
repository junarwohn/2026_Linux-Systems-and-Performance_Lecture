#include "../student_support/lmk_policy.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    lab_candidate tasks[] = {
        {20, 0, 900, 12800},
        {13, 700, 800, 6600},
        {15, 900, 820, 2200},
        {11, 900, 810, 2200},
        {10, -1000, 999, 99999},
    };
    assert(!lab_should_act(0, 0));
    assert(lab_should_act(0, 1));
    assert(lab_should_act(1, 0));
    assert(lab_choose(tasks, 5, 0, 900) == 0);
    assert(lab_choose(tasks, 5, 1, 900) == 3);
    assert(lab_choose(tasks, 2, 1, 900) == -1);
    assert(lab_choose(tasks, 2, 1, 700) == 1);
    assert(lab_choose(tasks + 4, 1, 0, 0) == -1);
    puts("trigger OR, score/importance ordering, tie, floor and -1000 checks passed");
}
