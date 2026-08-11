cmake_minimum_required(VERSION 3.22)
project(llmschedbench LANGUAGES CXX)

include(CTest)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
set(PYBIND11_FINDPYTHON ON)
find_package(pybind11 CONFIG REQUIRED)

add_library(llmschedbench_policy STATIC
    src/cpp/policy.cpp
)
target_include_directories(llmschedbench_policy PUBLIC src/cpp/include)
target_compile_options(llmschedbench_policy PRIVATE -Wall -Wextra -Wpedantic)
set_target_properties(llmschedbench_policy PROPERTIES POSITION_INDEPENDENT_CODE ON)

pybind11_add_module(_policy src/cpp/bindings.cpp)
target_link_libraries(_policy PRIVATE llmschedbench_policy)

install(TARGETS _policy DESTINATION llmschedbench)

if(BUILD_TESTING)
  add_executable(policy_test tests/cpp/test_policy.cpp)
  target_link_libraries(policy_test PRIVATE llmschedbench_policy)
  add_test(NAME policy_test COMMAND policy_test)
endif()
