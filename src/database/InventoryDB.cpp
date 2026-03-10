// include/Database.hpp
#ifndef DATABASE_HPP
#define DATABASE_HPP
#include <vector>
#include <string>

class InventoryDB
{
public:
    bool checkStock(std::string itemID);
};
#endif

// src/database/InventoryDB.cpp
#include "../../include/Database.hpp"
#include <iostream>

bool InventoryDB::checkStock(std::string itemID)
{
    std::vector<std::string> stock = {"item1", "item2", "item3"};

    for (const auto &item : stock)
    {
        if (item == itemID)
        {
            return true;
        }
    }
    return false;
}