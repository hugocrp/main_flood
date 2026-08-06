# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.16)

# Nothing else here sets a build type, and CMake defaults to none (i.e. no
# optimization flags at all) when unset -- for a physics loop this heavy,
# that's the difference between a run finishing in seconds vs minutes.
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

set(projectName "flood")

project(${projectName})
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/bin)
add_subdirectory(main)
add_subdirectory(${CMAKE_SOURCE_DIR}/lib/cadmium_v2)
target_link_libraries(${PROJECT_NAME} cadmium)
set_target_properties(${PROJECT_NAME} PROPERTIES CXX_STANDARD 17)
target_compile_definitions(${PROJECT_NAME} PRIVATE _USE_MATH_DEFINES)
