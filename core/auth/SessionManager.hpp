#ifndef SESSION_MANAGER_HPP
#define SESSION_MANAGER_HPP
#include <string>
#include <iostream>

class SessionManager
{
public:
    bool validateUser(std::string token)
    {
        if (token == "VALID_TOKEN")
        {
            std::cout << "Access Granted" << std::endl;
            return true;
        }
        else
        {
            std::cout << "Access Denied" << std::endl;
            return false;
        }
    }
};
#endif