# Example C++ agent brief

Each iteration:

1. Build the project:
   ```
   cmake -S . -B build -G Ninja 2>&1 | tail -5
   cmake --build build 2>&1 | tail -5
   ```
2. Run it: `./build/hello`
3. Edit `main.cpp` to print one more interesting line about C++ —
   keep total `std::cout` count below 20.
4. Exit. The driver loops you back.

Stop condition: when `main.cpp` already has 20 or more `std::cout`
calls, do nothing this iter and write `STOP` to `status.txt`.
