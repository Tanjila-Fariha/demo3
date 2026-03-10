#include "../../include/ShoppingCart.hpp"

void ShoppingCart::addItem(std::string id)
{
    // Testing 'alt' (if) logic detection
    if (id != "")
    {
        items.push_back(id);
    }
}

size_t ShoppingCart::getCount()
{
    return items.size();
}