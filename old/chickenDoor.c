#include <wiringPi.h>
#include <stdio.h>
#include <errno.h>
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>

#define COOPDOOR1	3
#define COOPDOOR2	22
#define RUNDOOR1	25
#define RUNDOOR2	7


typedef enum {
 STANDBY,
 OPEN,
 CLOSE
} doorCommand;

typedef enum {
	COOP,
	RUN,
	NONE
} chickenDoor;

void runCommand(chickenDoor door, doorCommand command){

	int relay1 = 0, relay2 = 0;
	int opendelay = 0, closedelay = 0;

	switch(door){
		case COOP:
			relay1 = COOPDOOR1;
			relay2 = COOPDOOR2;
			opendelay = 30000;
			closedelay = 30000;
			break;
		case RUN:
			relay1 = RUNDOOR1;
			relay2 = RUNDOOR2;
			opendelay = 30000;
			closedelay = 35000;
			break;
		default:
		    return;
	}

	switch(command){
		case STANDBY:
			printf("Door standby\n");
			digitalWrite(relay1, LOW);
			digitalWrite(relay2, LOW);
			break;
		case OPEN:
			printf("Door open\n");
			digitalWrite(relay1, HIGH);
			digitalWrite(relay2, LOW);
			delay(opendelay);
			break;
		case CLOSE:
			printf("Door close\n");
			digitalWrite(relay1, LOW);
			digitalWrite(relay2, HIGH);
			delay(closedelay);
			break;
	}

   digitalWrite(relay1, LOW);
   digitalWrite(relay2, LOW);

}

void signalHandler(int sigNum){
 runCommand(RUN, STANDBY);
 runCommand(COOP, STANDBY);
 exit(-1);
}

int main(int argc, char **argv){

  int ich;

  wiringPiSetup();

  	pinMode(COOPDOOR1, OUTPUT);
	pinMode(COOPDOOR2, OUTPUT);
	pinMode(RUNDOOR1, OUTPUT);
	pinMode(RUNDOOR2, OUTPUT);

  signal(SIGINT,  signalHandler);
  signal(SIGTERM, signalHandler);
  signal(SIGQUIT, signalHandler);
  signal(SIGPIPE, signalHandler);

  doorCommand command = STANDBY;
  chickenDoor door = NONE;

  while((ich = getopt(argc, argv, "ocsCRh")) != EOF){
    switch(ich){
      case 'o':
        command = OPEN;
        break;
      case 'c':
        command = CLOSE;
      	break;
      case 's':
        command = STANDBY;
        break;
      case 'C':
       	door = COOP;
        break;
      case 'R':
	door = RUN;
	break;
      case 'h':
        printf("Needs: command and door.  \n(o)pen, (c)lose, (s)tandby\n(R)un or (C)oop\n\n");
	exit(0);
     }
  }

  if(door == NONE){
    printf("No door selected\n");
    exit(-1);
  }
  runCommand(door, command);
  printf("Done\n");

}



