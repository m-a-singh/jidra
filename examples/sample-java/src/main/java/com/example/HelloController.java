package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class HelloController {
    private final GreeterService greeterService = new GreeterService();

    @GetMapping("/hello")
    public String hello() {
        return greeterService.message();
    }
}
