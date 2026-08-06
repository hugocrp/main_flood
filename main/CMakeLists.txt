add_executable(${projectName} main.cpp)
set(CADMIUM_DIR $ENV{CADMIUM})
if(NOT CADMIUM_DIR)
    set(CADMIUM_DIR ${CMAKE_SOURCE_DIR}/lib/cadmium_v2/include)
endif()
target_include_directories(${projectName} PUBLIC
    "."
    "include"
    ${CADMIUM_DIR}
    "${CADMIUM_DIR}/../json/include"
)
target_compile_options(${projectName} PUBLIC -std=gnu++2b)
