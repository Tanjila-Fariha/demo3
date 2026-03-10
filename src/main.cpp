#include "../include/Database.hpp"
#include "../include/ShoppingCart.hpp"
#include "../core/auth/SessionManager.hpp"
#include <iostream>

int main()
{
    SessionManager auth;
    InventoryDB db;
    ShoppingCart cart;

    std::string testItem = "Laptop_001";

    // Complex nested flow for the AST to trace
    if (auth.validateUser("VALID_TOKEN"))
    {
        if (db.checkStock(testItem))
        {
            cart.addItem(testItem);
            std::cout << "Item added to cart. Total: " << cart.getCount() << std::endl;
        }
        else
        {
            std::cout << "Out of stock!" << std::endl;
        }
    }

    return 0;
}