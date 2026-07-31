cmake_minimum_required(VERSION 3.22)
project(vapor_kpackage_discovery LANGUAGES CXX)

find_package(Qt6 6.6 REQUIRED COMPONENTS Core)
find_package(KF6CoreAddons REQUIRED)
find_package(KF6Package REQUIRED)

add_executable(
    kpackage-discovery-check
    kpackage_discovery_check.cpp
)
target_compile_features(kpackage-discovery-check PRIVATE cxx_std_20)
target_link_libraries(
    kpackage-discovery-check
    PRIVATE
        Qt6::Core
        KF6::CoreAddons
        KF6::Package
)
