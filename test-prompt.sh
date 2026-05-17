#!/bin/bash
# Test script: simulates a CLI tool asking for confirmation

echo "=== Test 1: Y/n prompt ==="
echo "Some processing done."
echo -n "Do you want to continue? (Y/n) "
read answer1
echo "You answered: $answer1"

echo ""
echo "=== Test 2: Confirm prompt ==="
echo "About to make changes..."
echo -n "Confirm? "
read answer2
echo "You answered: $answer2"

echo ""
echo "=== Test 3: Proceed prompt ==="
echo -n "Proceed with installation? "
read answer3
echo "You answered: $answer3"

echo ""
echo "=== All tests complete ==="
echo "Answers: 1=$answer1 2=$answer2 3=$answer3"
