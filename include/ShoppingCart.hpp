#ifndef SHOPPING_CART_HPP
#define SHOPPING_CART_HPP
#include <vector>
#include <string>

class ShoppingCart
{
private:
    std::vector<std::string> items;

public:
    void addItem(std::string id);
    size_t getCount();
};
#endif