// src/orders/OrderProcessor.hpp
#ifndef ORDER_PROCESSOR_HPP
#define ORDER_PROCESSOR_HPP
#include "../../include/Database.hpp"

class OrderProcessor
{
public:
    void processTransaction(int retryCount, std::string itemID);
};
#endif

// src/orders/OrderProcessor.cpp
#include "OrderProcessor.hpp"
#include <iostream>

void OrderProcessor::processTransaction(int retryCount, std::string itemID)
{
    InventoryDB db;
    int attempts = 0;

    if (retryCount > 0)
    {
        while (attempts < retryCount)
        {
            std::cout << "Attempting transaction..." << std::endl;
            if (db.checkStock(itemID))
            {
                std::cout << "Order Success!" << std::endl;
                break;
            }
            attempts++;
        }
    }
    else
    {
        std::cout << "Invalid retry count" << std::endl;
    }
}