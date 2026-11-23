#include "../student_support/lmk_policy.h"

int lab_should_act(int amount_low, int psi_event)
{
    return amount_low || psi_event;
}

int lab_choose(const lab_candidate *candidates, int count, int importance, int floor)
{
    int selected = -1;
    for (int i = 0; i < count; i++) {
        const lab_candidate *candidate = &candidates[i];
        if (candidate->adj == -1000 || (importance && candidate->adj < floor))
            continue;
        if (selected == -1) {
            selected = i;
            continue;
        }
        const lab_candidate *current = &candidates[selected];
        int priority = importance ? candidate->adj : candidate->score;
        int current_priority = importance ? current->adj : current->score;
        if (priority > current_priority ||
            (priority == current_priority &&
             (candidate->rss_kib > current->rss_kib ||
              (candidate->rss_kib == current->rss_kib && candidate->pid < current->pid))))
            selected = i;
    }
    return selected;
}
