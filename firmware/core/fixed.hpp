#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <string_view>

namespace tuner {
// Protocol fields and replies are strictly bounded before construction. An
// internal capacity violation halts; the RP2040 watchdog then resets disabled.
// No valid schema message approaches these capacities.
class Text {
public:
    static constexpr size_t capacity = 768;
    Text() = default;
    Text(const char *value) : Text(std::string_view(value)) {}
    Text(std::string_view value) { append(value); }
    Text &operator=(const char *value) { clear(); append(value); return *this; }
    Text &operator=(std::string_view value) { clear(); append(value); return *this; }
    const char *c_str() const { return bytes_.data(); }
    const char *data() const { return bytes_.data(); }
    size_t size() const { return used_; }
    bool empty() const { return used_ == 0; }
    void clear() { used_ = 0; bytes_[0] = 0; }
    const char *begin() const { return bytes_.data(); }
    const char *end() const { return bytes_.data() + used_; }
    operator std::string_view() const { return {data(), size()}; }
    Text &operator+=(std::string_view other) { append(other); return *this; }
    Text &add(std::string_view value) { append(value); return *this; }
    Text &number(int value) {
        char buffer[16];
        std::snprintf(buffer, sizeof(buffer), "%d", value);
        return add(buffer);
    }
    friend bool operator==(const Text &a, const Text &b) {
        return a.size() == b.size() && std::memcmp(a.data(), b.data(), a.size()) == 0;
    }
    friend bool operator!=(const Text &a, const Text &b) { return !(a == b); }
    friend bool operator==(const Text &a, const char *b) { return std::string_view(a) == std::string_view(b); }
    friend bool operator!=(const Text &a, const char *b) { return !(a == b); }
private:
    void append(std::string_view value) {
        if (value.size() > capacity - used_) std::abort();
        std::memcpy(bytes_.data() + used_, value.data(), value.size());
        used_ += value.size();
        bytes_[used_] = 0;
    }
    std::array<char, capacity + 1> bytes_{};
    size_t used_ = 0;
};

inline Text decimal(int value) {
    char buffer[16];
    std::snprintf(buffer, sizeof(buffer), "%d", value);
    return Text(buffer);
}

template <class T, size_t Capacity> class FixedList {
public:
    FixedList() = default;
    FixedList(std::initializer_list<T> values) { for (const auto &value : values) push_back(value); }
    void push_back(const T &value) {
        if (used_ == Capacity) std::abort();
        items_[used_++] = value;
    }
    size_t size() const { return used_; }
    size_t remaining() const { return Capacity - used_; }
    bool empty() const { return used_ == 0; }
    void clear() { used_ = 0; }
    T *begin() { return items_.data(); }
    T *end() { return items_.data() + used_; }
    const T *begin() const { return items_.data(); }
    const T *end() const { return items_.data() + used_; }
    T &operator[](size_t i) { return items_[i]; }
    const T &operator[](size_t i) const { return items_[i]; }
private:
    std::array<T, Capacity> items_{};
    size_t used_ = 0;
};
using Events = FixedList<Text, 4>;
using CachedReplies = FixedList<Text, 2>;
} // namespace tuner
