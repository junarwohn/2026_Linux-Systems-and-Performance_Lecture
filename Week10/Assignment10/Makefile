CC ?= cc
CFLAGS ?= -O0 -g3 -Wall -Wextra \
    -Wno-unused-function -fno-omit-frame-pointer -fno-optimize-sibling-calls

all: scenario foreground

scenario: scenario_src/scenario.c scenario_src/common.h
	$(CC) $(CFLAGS) -rdynamic -o $@ scenario_src/scenario.c

foreground: student_support/foreground.c scenario_src/common.h
	$(CC) $(CFLAGS) -o $@ student_support/foreground.c

clean:
	rm -f scenario foreground scenario-fixed alloctrace.so memhog psimon

.PHONY: all clean


all: scenario-fixed alloctrace.so memhog psimon

scenario-fixed: scenario_src/scenario_fixed.c scenario_src/common.h
	$(CC) $(CFLAGS) -rdynamic -o $@ $<

alloctrace.so: src/alloctrace.c
	$(CC) -std=c11 -O2 -g -Wall -Wextra -Werror -fPIC -shared -o $@ $< -ldl -pthread

WEEK9 = ../../Week9/Assignment9
memhog: $(WEEK9)/src/memhog.c
	$(CC) -std=c11 -O2 -g -Wall -Wextra -Werror -o $@ $<

psimon: $(WEEK9)/src/psimon.c
	$(CC) -std=c11 -O2 -g -Wall -Wextra -Werror -o $@ $<
